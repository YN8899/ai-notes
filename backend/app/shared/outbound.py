"""Bounded HTTPS egress. Credentials and upstream errors must never be logged.

Resolve ALL addresses on every send, then connect to one validated IP while
retaining the original Host and TLS server name. Never delegate DNS to a second
HTTP client resolution (DNS rebinding), or follow redirects/system proxies.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from queue import Queue
from threading import BoundedSemaphore, Event, Lock, Thread, Timer
from typing import Callable
import ipaddress
import logging
import math
import re
import socket
import time
from urllib.parse import urlsplit

import httpx
import httpcore


_HTTP_TRANSPORT = httpx.HTTPTransport
_sensitive_send = ContextVar("sensitive_outbound_send", default=False)


class _PrivateTransportLogs(logging.Filter):
    def filter(self, record):
        # httpcore DEBUG includes raw upstream headers (e.g. Location) and
        # exception text. Suppress only this request/thread, not other traffic.
        return not _sensitive_send.get()


for _logger_name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
    logging.getLogger(_logger_name).addFilter(_PrivateTransportLogs())


class UnsafeDestination(ValueError):
    def __init__(self):
        super().__init__("outbound_destination_rejected")


class EndpointUnavailable(RuntimeError):
    def __init__(self, reason="unreachable"):
        self.reason = reason if reason in {"unreachable", "timeout", "invalid_response"} else "unreachable"
        super().__init__("outbound_endpoint_unavailable")


class SendCancelled(RuntimeError):
    def __init__(self):
        super().__init__("outbound_send_cancelled")


DNS_TIMEOUT_SECONDS = 2.0
DNS_WORKERS = 4


class _ResolverPool:
    """OS DNS cannot be cancelled: bound both caller wait and stuck work.

    Timed-out lookups retain their slot until the OS call returns. Saturation
    fails closed instead of growing threads/a pending queue. Daemon workers do
    not make process shutdown wait for an unresponsive system resolver.
    """
    def __init__(self, workers=DNS_WORKERS):
        self._count = workers
        self._slots = BoundedSemaphore(workers)
        self._queue = Queue(maxsize=workers)
        self._start_lock = Lock()
        self._threads = []

    def _work(self):
        while True:
            resolver, host, done, result = self._queue.get()
            try:
                result.append(resolver(host, 443, type=socket.SOCK_STREAM))
            except Exception:
                # Do not retain/propagate exception strings or resolver inputs.
                pass
            finally:
                self._slots.release()
                done.set()
                self._queue.task_done()

    def lookup(self, host, timeout):
        if timeout <= 0 or not self._slots.acquire(blocking=False):
            raise EndpointUnavailable()
        try:
            with self._start_lock:
                while len(self._threads) < self._count:
                    thread = Thread(target=self._work, name="notes-outbound-dns", daemon=True)
                    thread.start()
                    self._threads.append(thread)
            done, result = Event(), []
            # Capture the resolver used for THIS request; a timed-out task never
            # runs any send callback and can only return a discarded DNS result.
            self._queue.put_nowait((socket.getaddrinfo, host, done, result))
        except Exception:
            self._slots.release()
            raise EndpointUnavailable() from None
        if not done.wait(timeout) or not result:
            raise EndpointUnavailable()
        return result[0]


_resolver_pool = _ResolverPool()


@dataclass(frozen=True, repr=False)
class Destination:
    host: str
    path: str


def validate_url(url: str) -> Destination:
    try:
        if (not isinstance(url, str) or not 1 <= len(url) <= 4096
                or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                or "\\" in url or "?" in url or "#" in url
                or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|20|7f)", url, re.IGNORECASE)):
            raise UnsafeDestination()
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.netloc not in {host, host + ":443"}
                or parsed.port not in {None, 443} or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or len(host) > 253 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split("."))):
            raise UnsafeDestination()
        # DNS names only; literal IPs are never a service identity.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise UnsafeDestination()
        return Destination(host, parsed.path or "/")
    except (ValueError, TypeError):
        raise UnsafeDestination() from None


def resolve_public(host: str, *, timeout: float | None = None) -> tuple[str, ...]:
    try:
        wait = min(DNS_TIMEOUT_SECONDS, timeout if timeout is not None else DNS_TIMEOUT_SECONDS)
        answers = _resolver_pool.lookup(host, wait)
        addresses = tuple(dict.fromkeys(row[4][0] for row in answers))
        if not addresses:
            raise UnsafeDestination()
        for raw in addresses:
            ip = ipaddress.ip_address(raw)
            if (not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved
                    or ip.is_loopback or ip.is_link_local or getattr(ip, "is_site_local", False)
                    or (isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped is not None
                        or ip.sixtofour is not None or ip.teredo is not None
                        or ip in ipaddress.ip_network("64:ff9b::/96")
                        or ip in ipaddress.ip_network("64:ff9b:1::/48")))):
                raise UnsafeDestination()
        return addresses
    except (OSError, ValueError):
        raise UnsafeDestination() from None


def _operation_timeout(deadline, timeout, error):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise error()
    return min(remaining, timeout) if timeout is not None else remaining


class _DeadlineStream(httpcore.NetworkStream):
    """One absolute budget across TCP, TLS, every header/body read and write.

    SyncStream.read performs one recv; TLS handshake uses Python's cumulative
    handshake timeout. SyncStream.write, however, loops over partial sends with
    a fresh timeout. Use individual socket sends and recompute before each one.
    """
    def __init__(self, stream, deadline):
        self._stream, self._deadline = stream, deadline

    def read(self, max_bytes, timeout=None):
        return self._stream.read(max_bytes, timeout=_operation_timeout(
            self._deadline, timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        sock = self._stream.get_extra_info("socket")
        view = memoryview(buffer)
        try:
            while view:
                sock.settimeout(_operation_timeout(self._deadline, timeout, httpcore.WriteTimeout))
                sent = sock.send(view)
                if sent <= 0:
                    raise httpcore.WriteError()
                view = view[sent:]
        except socket.timeout:
            raise httpcore.WriteTimeout() from None
        except OSError:
            raise httpcore.WriteError() from None

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        stream = self._stream.start_tls(ssl_context, server_hostname=server_hostname,
            timeout=_operation_timeout(self._deadline, timeout, httpcore.ConnectTimeout))
        return _DeadlineStream(stream, self._deadline)

    def close(self):
        self._stream.close()

    def get_extra_info(self, info):
        return self._stream.get_extra_info(info)


class _DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, backend, deadline):
        self._backend, self._deadline = backend, deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = self._backend.connect_tcp(host, port, timeout=_operation_timeout(
            self._deadline, timeout, httpcore.ConnectTimeout), local_address=local_address,
            socket_options=socket_options)
        return _DeadlineStream(stream, self._deadline)


def _install_deadline(transport, deadline):
    # HTTPX 0.28.1 exposes no network_backend constructor argument. This small
    # version-bound bridge installs the public HTTPCore 1.0.9 backend interface
    # on its fresh pool, before any connection or final authorization gate.
    # HTTPTransport remains the injection seam for offline MockTransport tests.
    pool = getattr(transport, "_pool", None)
    if pool is None and isinstance(transport, _HTTP_TRANSPORT):
        raise EndpointUnavailable()  # An incompatible real HTTPX upgrade fails closed.
    if pool is not None:
        if (httpx.__version__ != "0.28.1" or httpcore.__version__ != "1.0.9"
                or type(pool) is not httpcore.ConnectionPool or pool.connections
                or type(getattr(pool, "_network_backend", None)) is not httpcore.SyncBackend):
            raise EndpointUnavailable()
        pool._network_backend = _DeadlineBackend(pool._network_backend, deadline)


def post(url: str, *, data: bytes, headers: dict, timeout: float = 10.0,
         before_send: Callable[[], bool] | None = None) -> httpx.Response:
    if not math.isfinite(timeout) or timeout <= 0:
        raise EndpointUnavailable()
    deadline = time.monotonic() + min(10.0, timeout)
    destination = validate_url(url)
    ip = resolve_public(destination.host, timeout=min(10.0, timeout))[0]
    # Transport API avoids Client's INFO URL logging (the path is a capability).
    # It also returns only headers: no arbitrary or unbounded error body is read.
    target = httpx.URL(url).copy_with(host=ip)
    safe_headers = {key: value for key, value in headers.items() if key.lower() != "host"}
    request = httpx.Request("POST", target, content=data, headers={**safe_headers, "Host": destination.host},
        extensions={"sni_hostname": destination.host, "timeout": dict.fromkeys(("connect", "read", "write", "pool"), min(10.0, timeout))})
    token = _sensitive_send.set(True)
    try:
        with httpx.HTTPTransport(verify=True, trust_env=False, retries=0) as transport:
            _install_deadline(transport, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EndpointUnavailable()
            request.extensions["timeout"] = dict.fromkeys(("connect", "read", "write", "pool"), remaining)
            # The final gate MUST follow DNS, request construction and transport
            # setup. No resolution or other preparatory work follows this check.
            if before_send is not None and not before_send():
                raise SendCancelled()
            if time.monotonic() >= deadline:
                raise EndpointUnavailable("timeout")
            response = transport.handle_request(request)
            try:
                if time.monotonic() >= deadline:
                    raise RuntimeError("outbound_delivery_unknown")
                return httpx.Response(response.status_code, content=b"")
            finally:
                response.close()
    except (EndpointUnavailable, SendCancelled):
        raise
    except Exception:
        # Avoid propagating httpx request objects containing endpoint paths.
        raise RuntimeError("outbound_delivery_unknown") from None
    finally:
        _sensitive_send.reset(token)


def stream(url: str, *, method: str = "POST", data: bytes = b"", headers: dict | None = None,
           timeout: float = 180.0, max_bytes: int = 2_000_000, max_line_bytes: int = 65536,
           before_send: Callable[[], bool] | None = None, response_type: str | None = None):
    """Yield (status, bounded raw chunk) while owning transport and log privacy.

    This generator must be closed by the consumer on cancellation. Unlike post(),
    it reads bounded response content; it never follows redirects or retries.
    The gate follows DNS, Request and Transport construction immediately before
    handle_request; callers persist their attempt before creating this iterator.
    """
    if method not in {"GET", "POST"} or not math.isfinite(timeout) or timeout <= 0 or response_type not in {None, "sse", "json"}:
        raise EndpointUnavailable()
    if not 1 <= max_bytes <= 2 * 1024 * 1024 or not 1 <= max_line_bytes <= (2 * 1024 * 1024 if response_type == "json" else 65536) or len(data) > 24 * 1024 * 1024:
        raise EndpointUnavailable()
    deadline = time.monotonic() + min(timeout, 180.0)
    destination = validate_url(url)
    ip = resolve_public(destination.host, timeout=min(DNS_TIMEOUT_SECONDS, timeout))[0]
    target = httpx.URL(url).copy_with(host=ip)
    safe_headers = {key: value for key, value in (headers or {}).items()
                    if key.lower() not in {"host", "accept-encoding", "connection"}}
    request = httpx.Request(method, target, content=data, headers={**safe_headers,
        "Host": destination.host, "Accept-Encoding": "identity", "Connection": "close"},
        extensions={"sni_hostname": destination.host})
    token = _sensitive_send.set(True)
    try:
        with httpx.HTTPTransport(verify=True, trust_env=False, retries=0) as transport:
            _install_deadline(transport, deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EndpointUnavailable()
            request.extensions["timeout"] = dict.fromkeys(("connect", "read", "write", "pool"), min(20.0, remaining))
            if before_send is not None and not before_send():
                raise SendCancelled()
            if time.monotonic() >= deadline:
                raise EndpointUnavailable("timeout")
            response = transport.handle_request(request)
            deadline_expired = Event()
            def abort_at_deadline():
                private_token = _sensitive_send.set(True)
                deadline_expired.set()
                try:
                    stream = response.extensions.get("network_stream")
                    raw_socket = stream.get_extra_info("socket") if stream else None
                    if raw_socket is not None:
                        raw_socket.shutdown(socket.SHUT_RDWR)
                    response.close()
                except Exception:
                    pass
                finally:
                    _sensitive_send.reset(private_token)
            timer = Timer(max(0.001, deadline - time.monotonic()), abort_at_deadline)
            timer.daemon = True
            timer.start()
            try:
                # Never read an upstream error body or a redirect Location.
                if response.status_code != 200:
                    yield response.status_code, b""
                    return
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                expected = {"text/event-stream"} if (response_type == "sse" or response_type is None and method == "POST") else {"application/json"}
                if content_type not in expected or response.headers.get("content-encoding", "identity") != "identity":
                    raise EndpointUnavailable("invalid_response")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isascii() or not declared.isdigit() or int(declared) > max_bytes):
                    raise EndpointUnavailable("invalid_response")
                total, pending_line = 0, 0
                for chunk in response.iter_raw():
                    total += len(chunk)
                    if deadline_expired.is_set() or time.monotonic() > deadline:
                        raise EndpointUnavailable("timeout")
                    if total > max_bytes:
                        raise EndpointUnavailable("invalid_response")
                    # Check every physical line before callers parse SSE/JSON.
                    parts = chunk.split(b"\n")
                    if len(parts) == 1:
                        pending_line += len(chunk)
                        if pending_line > max_line_bytes:
                            raise EndpointUnavailable("invalid_response")
                    else:
                        if pending_line + len(parts[0]) > max_line_bytes or any(len(p) > max_line_bytes for p in parts[1:]):
                            raise EndpointUnavailable("invalid_response")
                        pending_line = len(parts[-1])
                    yield response.status_code, chunk
                if deadline_expired.is_set() or time.monotonic() > deadline:
                    raise EndpointUnavailable("timeout")
            finally:
                timer.cancel()
                response.close()
    except (EndpointUnavailable, UnsafeDestination, SendCancelled):
        raise
    except httpx.TimeoutException:
        raise EndpointUnavailable("timeout") from None
    except Exception:
        raise EndpointUnavailable("timeout" if time.monotonic() >= deadline else "unreachable") from None
    finally:
        _sensitive_send.reset(token)


def get_json(url: str, *, headers: dict, before_send=None) -> tuple[int, bytes]:
    content = bytearray()
    status = 0
    iterator = stream(url, method="GET", headers=headers, timeout=10, max_bytes=65536,
                      max_line_bytes=65536, before_send=before_send)
    try:
        for status, chunk in iterator:
            content.extend(chunk)
        return status, bytes(content)
    finally:
        iterator.close()



def post_json(url: str, *, data: bytes, headers: dict, timeout=40.0, before_send=None) -> tuple[int, bytes]:
    content, status = bytearray(), 0
    iterator = stream(url, method="POST", data=data, headers=headers, timeout=timeout,
        max_bytes=2 * 1024 * 1024, max_line_bytes=2 * 1024 * 1024, before_send=before_send, response_type="json")
    try:
        for status, chunk in iterator:
            content.extend(chunk)
        return status, bytes(content)
    finally:
        iterator.close()
