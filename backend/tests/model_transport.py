"""Central HTTPTransport fakes, independently scoped to chat/discovery/embedding."""
import httpx
from app.shared import outbound


def install_model_transport(monkeypatch, handler, *, kind="chat"):
    registry = getattr(monkeypatch, "_notes_model_transports", None)
    if registry is None:
        registry = {}
        setattr(monkeypatch, "_notes_model_transports", registry)
    registry[kind] = handler
    def controlled(request):
        selected = "embedding" if request.url.path.endswith("/embeddings") else "discovery" if request.method == "GET" else "chat"
        if selected not in registry:
            raise AssertionError("No fake installed for this provider operation")
        response = registry[selected](request)
        headers = dict(response.headers)
        if response.status_code == 200:
            headers.setdefault("content-type", "text/event-stream" if selected == "chat" else "application/json")
        if response.is_stream_consumed:
            return httpx.Response(response.status_code, headers=headers, stream=httpx.ByteStream(response.content))
        response.headers.update(headers)
        return response
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    monkeypatch.setattr(outbound.httpx, "HTTPTransport", lambda **kw: httpx.MockTransport(controlled))
