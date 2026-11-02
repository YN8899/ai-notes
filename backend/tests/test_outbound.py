import socket
import logging
import subprocess
import sys
import threading
import time
import httpx
import pytest
from app.shared import outbound


@pytest.mark.parametrize("url", [
    "http://fcm.googleapis.com/send/secret", "https://user@fcm.googleapis.com/secret",
    "https://fcm.googleapis.com:444/secret", "https://fcm.googleapis.com/?secret",
    "https://fcm.googleapis.com/#x", "https://fcm.googleapis.com/\\x",
    "https://fcm.googleapis.com/\nsecret", "https://127.0.0.1/x", "https://[::1]/x",
    "https://fcm.googleapis.com./x", "https://fcm.googleapis.com:/x",
])
def test_url_rejects_ambiguous_and_unsafe_destinations(url):
    with pytest.raises(outbound.UnsafeDestination):
        outbound.validate_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "::1", "fc00::1", "fe80::1", "fec0::1", "::ffff:8.8.8.8", "2002:0808:0808::1", "64:ff9b::808:808", "2001::1", "224.0.0.1"])
def test_all_dns_answers_must_be_public_including_ipv6(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443)), (socket.AF_INET6, 1, 6, '', (ip, 443))])
    with pytest.raises(outbound.UnsafeDestination):
        outbound.resolve_public("fcm.googleapis.com")


def test_rebinding_cannot_change_actual_connection_tls_name_or_follow_redirect(monkeypatch):
    resolved, calls, options = [], [], []
    def resolve(*args, **kwargs):
        resolved.append(args)
        ip='8.8.8.8' if len(resolved)==1 else '127.0.0.1'
        return [(socket.AF_INET, 1, 6, '', (ip,443))]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    class Transport:
        def __init__(self, **kw): options.append(kw)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def handle_request(self, request):
            calls.append(request)
            # A second resolver lookup now returns localhost, but URL is pinned.
            socket.getaddrinfo('fcm.googleapis.com',443)
            assert request.url.host=='8.8.8.8'
            assert request.headers['host']=='fcm.googleapis.com'
            assert request.extensions['sni_hostname']=='fcm.googleapis.com'
            return httpx.Response(307, headers={'location':'https://127.0.0.1/private'}, content=b'capability-secret')
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    response=outbound.post('https://fcm.googleapis.com/send/secret',data=b'encrypted',headers={'Host':'evil.test'})
    assert response.status_code==307 and response.content==b''
    assert len(calls)==1 and options==[{'verify':True,'trust_env':False,'retries':0}]
    # Every NEW send re-resolves and rejects the rebound answer.
    with pytest.raises(outbound.UnsafeDestination):
        outbound.post('https://fcm.googleapis.com/send/secret',data=b'',headers={})


def test_transport_failure_sanitizes_request_and_exception(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(outbound, 'resolve_public', lambda host, **kwargs: ('8.8.8.8',))
    class Transport:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def handle_request(self,request):
            logging.getLogger('httpcore.http11').debug('Location: secret-endpoint-with-auth')
            logging.getLogger('httpx').info('https://fcm.googleapis.com/secret-endpoint-with-auth')
            raise RuntimeError('secret-endpoint-with-auth')
    monkeypatch.setattr(httpx, 'HTTPTransport', Transport)
    with pytest.raises(RuntimeError, match='^outbound_delivery_unknown$'):
        outbound.post('https://fcm.googleapis.com/secret-endpoint-with-auth',data=b'',headers={})
    assert 'secret-endpoint' not in caplog.text
    logging.getLogger('httpcore.http11').debug('unrelated request remains observable')
    assert 'unrelated request remains observable' in caplog.text


@pytest.mark.parametrize('allowed',[False,True])
def test_final_gate_follows_dns_request_and_transport_setup_and_precedes_only_post(monkeypatch,allowed):
    events=[];requests=[]
    def resolve(*args,**kwargs):
        events.append('dns')
        return [(socket.AF_INET,1,6,'',('8.8.8.8',443))]
    class Transport:
        def __init__(self,**kwargs):events.append('transport')
        def __enter__(self):events.append('enter');return self
        def __exit__(self,*args):events.append('exit')
        def handle_request(self,request):
            events.append('post');requests.append(request)
            return httpx.Response(201)
    def gate():events.append('gate');return allowed
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    if allowed:
        assert outbound.post('https://fcm.googleapis.com/send/secret',data=b'encrypted',headers={},before_send=gate).status_code==201
        assert events==['dns','transport','enter','gate','post','exit'] and len(requests)==1
    else:
        with pytest.raises(outbound.SendCancelled,match='^outbound_send_cancelled$'):
            outbound.post('https://fcm.googleapis.com/send/secret',data=b'encrypted',headers={},before_send=gate)
        assert events==['dns','transport','enter','gate','exit'] and requests==[]


def test_dns_wait_and_global_concurrency_remain_bounded_and_late_answers_never_send(monkeypatch):
    release=threading.Event();completed=[threading.Event(),threading.Event()];calls=[]
    pool=outbound._ResolverPool(workers=2)
    def resolve(*args,**kwargs):
        number=len(calls);calls.append(threading.current_thread())
        try:
            release.wait()
            return [(socket.AF_INET,1,6,'',('8.8.8.8',443))]
        finally:
            if number<2:completed[number].set()
    monkeypatch.setattr(outbound,'_resolver_pool',pool)
    monkeypatch.setattr(outbound,'DNS_TIMEOUT_SECONDS',.03)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    sends=[]
    monkeypatch.setattr(httpx,'HTTPTransport',lambda **kw:sends.append(kw))
    try:
        for _ in range(2):
            started=time.monotonic()
            with pytest.raises(outbound.EndpointUnavailable,match='^outbound_endpoint_unavailable$'):
                outbound.post('https://fcm.googleapis.com/send/secret',data=b'',headers={})
            assert time.monotonic()-started<.5
        assert len(calls)==2 and all(thread.daemon for thread in calls)
        started=time.monotonic()
        for _ in range(50):
            with pytest.raises(outbound.EndpointUnavailable):outbound.resolve_public('fcm.googleapis.com')
        assert time.monotonic()-started<.5 and len(calls)==2
        assert len(pool._threads)==2 and sends==[]
    finally:
        release.set()
        assert all(event.wait(1) for event in completed)
    # Completed OS lookups release capacity but cannot resume expired sends.
    assert pool._slots.acquire(timeout=1)
    pool._slots.release()
    assert outbound.resolve_public('fcm.googleapis.com')==('8.8.8.8',)
    assert len(pool._threads)==2 and sends==[]


def test_stuck_daemon_resolver_does_not_block_process_shutdown():
    source='''
import socket, threading
from app.shared.outbound import _ResolverPool, EndpointUnavailable
socket.getaddrinfo = lambda *a, **kw: threading.Event().wait()
pool = _ResolverPool(workers=1)
try:
    pool.lookup("fcm.googleapis.com", .02)
except EndpointUnavailable:
    print("dns_wait_bounded", flush=True)
'''
    completed=subprocess.run([sys.executable,'-c',source],capture_output=True,text=True,timeout=3)
    assert completed.returncode==0 and completed.stdout.strip()=='dns_wait_bounded'
    assert completed.stderr==''


def test_dns_errors_and_gate_errors_never_expose_resolver_or_endpoint_text(monkeypatch,caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:(_ for _ in ()).throw(socket.gaierror('endpoint-with-private-capability')))
    with pytest.raises(outbound.EndpointUnavailable,match='^outbound_endpoint_unavailable$'):
        outbound.resolve_public('fcm.googleapis.com')
    monkeypatch.setattr(outbound,'resolve_public',lambda host,**kw:('8.8.8.8',))
    class Transport:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def handle_request(self,request):raise AssertionError('gate must prevent send')
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    def gate():raise RuntimeError('endpoint-with-private-capability')
    with pytest.raises(RuntimeError,match='^outbound_delivery_unknown$'):
        outbound.post('https://fcm.googleapis.com/endpoint-with-private-capability',data=b'',headers={},before_send=gate)
    assert 'endpoint-with-private-capability' not in caplog.text


def test_exhausted_preparation_deadline_never_calls_gate_or_transport_send(monkeypatch):
    clock=iter([0.,11.]);calls=[]
    monkeypatch.setattr(outbound.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(outbound,'resolve_public',lambda host,**kw:('8.8.8.8',))
    class Transport:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def handle_request(self,request):calls.append('send');return httpx.Response(201)
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    with pytest.raises(outbound.EndpointUnavailable):
        outbound.post('https://fcm.googleapis.com/send/secret',data=b'',headers={},before_send=lambda:calls.append('gate'))
    assert calls==[]
