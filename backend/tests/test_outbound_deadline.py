"""Real loopback TLS + HTTPTransport: absolute deadlines and resource release.

No external DNS/network, system trust changes, server process or real credential.
Only the test resolver and pinned destination port are redirected to loopback.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import socket
import ssl
import threading
import time

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.shared import outbound


@pytest.fixture
def tls_contexts(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    seen_names = []
    server.set_servername_callback(lambda sock, name, ctx: seen_names.append(name))
    return server, ssl.create_default_context(cafile=str(cert_path)), seen_names


@contextmanager
def local_tls(monkeypatch, contexts, handler, *, handshake=True):
    server_ctx, client_ctx, names = contexts
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    stop, done = threading.Event(), threading.Event()
    sockets, requests, errors = [], [], []

    def serve():
        try:
            raw, _ = listener.accept()
            raw.settimeout(2)
            sockets.append(raw)
            if not handshake:
                stop.wait(1)
                return
            with server_ctx.wrap_socket(raw, server_side=True) as sock:
                sockets.append(sock)
                handler(sock, stop, requests)
        except (OSError, ssl.SSLError):
            # Deadline cancellation closes the client connection mid-write/read.
            pass
        except Exception as error:
            errors.append(error)
        finally:
            for sock in sockets:
                sock.close()
            listener.close()
            done.set()

    original = httpx.HTTPTransport

    class LocalTransport(original):
        def __init__(self, **kwargs):
            assert kwargs == {"verify": True, "trust_env": False, "retries": 0}
            super().__init__(verify=client_ctx, trust_env=False, retries=0)

        def handle_request(self, request):
            assert isinstance(self._pool._network_backend, outbound._DeadlineBackend)
            assert request.url.host == "127.0.0.1"
            assert request.headers["host"] == "localhost"
            assert request.extensions["sni_hostname"] == "localhost"
            request.url = request.url.copy_with(port=port)
            return super().handle_request(request)

    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("127.0.0.1",))
    monkeypatch.setattr(httpx, "HTTPTransport", LocalTransport)
    thread = threading.Thread(target=serve, name="test-loopback-tls", daemon=True)
    thread.start()
    try:
        yield requests, names, done
    finally:
        stop.set()
        listener.close()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        thread.join(2)
        assert not thread.is_alive() and not errors


def read_request(sock, requests):
    content = b""
    while b"\r\n\r\n" not in content:
        chunk = sock.recv(65536)
        if not chunk:
            return
        content += chunk
    requests.append(content)


@pytest.mark.parametrize("operation,phase", [(operation, phase)
    for phase in ("headers", "body", "tls", "write")
    for operation in ("stream", "post", "json_get", "json_post")
    if (operation, phase) != ("post", "body")])
def test_real_tls_absolute_deadline(monkeypatch, tls_contexts, operation, phase):
    def server(sock, stop, requests):
        read_request(sock, requests)
        if phase == "write":
            # Stop reading the 16MiB upload after the request header. The client
            # must time out in the write, not wait for a response header.
            stop.wait(1)
            return
        if phase == "headers":
            sock.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            for _ in range(16):
                if stop.wait(.04):
                    return
                sock.sendall(b"a")
            sock.sendall(b"\r\nContent-Length: 0\r\n\r\n")
        else:
            content_type = b"application/json" if operation.startswith("json") else b"text/event-stream"
            sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type + b"\r\nContent-Length: 16\r\n\r\n")
            for _ in range(16):
                if stop.wait(.04):
                    return
                sock.sendall(b"a")

    with local_tls(monkeypatch, tls_contexts, server, handshake=phase != "tls") as (requests, names, done):
        started = time.monotonic()
        data = b"x" * (16 * 1024 * 1024) if phase == "write" else b"{}"
        if operation == "post":
            with pytest.raises(RuntimeError, match="^outbound_delivery_unknown$"):
                outbound.post("https://localhost/test", data=data, headers={}, timeout=.2)
        else:
            # Exercise the common bounded stream exactly as both JSON adapters
            # use it, while reducing only their otherwise fixed 10/40s budget.
            with pytest.raises(outbound.EndpointUnavailable) as error:
                list(outbound.stream("https://localhost/test", data=data,
                    method="GET" if operation == "json_get" else "POST", timeout=.2,
                    response_type="json" if operation.startswith("json") else "sse"))
            assert error.value.reason == "timeout"
        elapsed = time.monotonic() - started
        print(f"real_tls {operation} {phase} elapsed={elapsed:.3f}s budget=0.200s")
        assert elapsed <= .35
        if phase != "tls":
            assert names == ["localhost"] and len(requests) == 1


def test_real_tls_consumer_close_releases_socket_without_lingering_watchdog(monkeypatch, tls_contexts):
    disconnected = threading.Event()

    def server(sock, stop, requests):
        read_request(sock, requests)
        sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 1024\r\n\r\nhello")
        if sock.recv(1) == b"":
            disconnected.set()

    before = {thread.ident for thread in threading.enumerate()}
    with local_tls(monkeypatch, tls_contexts, server) as (_, _, done):
        iterator = outbound.stream("https://localhost/test", timeout=1)
        assert next(iterator) == (200, b"hello")
        iterator.close()
        assert disconnected.wait(.5) and done.wait(.5)
    # Timer.cancel is asynchronous; allow the cancelled thread to exit, with a
    # bounded wait, then prove repeated requests cannot accumulate watchdogs.
    until = time.monotonic() + .5
    while any(t.ident not in before for t in threading.enumerate()) and time.monotonic() < until:
        time.sleep(.005)
    assert {t.ident for t in threading.enumerate()} <= before


def test_real_tls_paused_consumer_deadline_releases_connection(monkeypatch, tls_contexts):
    disconnected = threading.Event()

    def server(sock, stop, requests):
        read_request(sock, requests)
        sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 1024\r\n\r\nhello")
        if sock.recv(1) == b"":
            disconnected.set()

    with local_tls(monkeypatch, tls_contexts, server):
        iterator = outbound.stream("https://localhost/test", timeout=.2)
        assert next(iterator) == (200, b"hello")
        assert disconnected.wait(.35)
        with pytest.raises(outbound.EndpointUnavailable) as error:
            next(iterator)
        assert error.value.reason == "timeout"
        iterator.close()


def test_partial_writes_recompute_remaining_for_every_send():
    # Socket-like unit seam targets SyncStream's internal partial-send loophole;
    # the real TLS stalled-upload cases above prove the actual network path.
    class PartialSocket:
        def __init__(self):
            self.timeouts = []
        def settimeout(self, value):
            self.timeouts.append(value)
        def send(self, data):
            time.sleep(.03)
            return 1
    sock = PartialSocket()
    stream = type("Stream", (), {"get_extra_info": lambda self, key: sock})()
    deadline = outbound._DeadlineStream(stream, time.monotonic() + .1)
    with pytest.raises(httpcore.WriteTimeout):
        deadline.write(b"0123456789", timeout=1)
    assert len(sock.timeouts) <= 4
    assert all(a > b for a, b in zip(sock.timeouts, sock.timeouts[1:]))


@pytest.mark.parametrize("change", ["httpx_version", "httpcore_version", "missing_pool", "changed_backend"])
def test_real_transport_incompatible_bridge_fails_closed(monkeypatch, change):
    with outbound._HTTP_TRANSPORT(verify=True, trust_env=False, retries=0) as transport:
        pool = transport._pool
        backend = pool._network_backend
        try:
            if change == "httpx_version":
                monkeypatch.setattr(httpx, "__version__", "unsupported")
            elif change == "httpcore_version":
                monkeypatch.setattr(httpcore, "__version__", "unsupported")
            elif change == "missing_pool":
                transport._pool = None
            else:
                pool._network_backend = object()
            with pytest.raises(outbound.EndpointUnavailable):
                outbound._install_deadline(transport, time.monotonic() + 1)
            assert not pool.connections
        finally:
            transport._pool = pool
            pool._network_backend = backend
