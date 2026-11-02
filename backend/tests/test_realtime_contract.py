"""Pure tests: no app.settings, real DB, environment file, socket or network."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import threading
import types
from uuid import uuid4

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
for name, directory in (("t34_social", APP / "modules/social"), ("t34_jobs", APP / "modules/jobs")):
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules[name] = package
signal = importlib.import_module("t34_social.realtime_signal")
listener = importlib.import_module("t34_social.realtime_listener")
hub_module = importlib.import_module("t34_social.realtime_hub")
socket_module = importlib.import_module("t34_social.realtime_socket")
lanes = importlib.import_module("t34_jobs.lanes")


@pytest.mark.parametrize("kind", sorted(signal.KINDS))
def test_metadata_roundtrip_contains_no_content(kind):
    owner = uuid4()
    value = signal.encode(owner, kind)
    assert signal.decode(value) == (owner, kind)
    assert set(json.loads(value)) == {"v", "owner", "kind"}
    assert len(value.encode()) <= 256


@pytest.mark.parametrize("value", ["", "[]", "null", "{}", "x"*257,
    '{"v":true,"owner":"x","kind":"chat"}', '{"v":1,"owner":"x","kind":[]}',
    '{"v":1,"owner":"x","kind":"chat","body":"secret"}',
    '{"v":2,"owner":"x","kind":"chat"}'])
def test_untrusted_signals_are_ignored(value):
    assert signal.decode(value) is None


class FakeLoop:
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, callback, *args):
        self.calls.append((callback, args))

    def flush(self):
        values, self.calls = self.calls, []
        for callback, args in values:
            callback(*args)


def test_bridge_bounds_owners_and_event_loop_callbacks_and_resyncs_overflow():
    loop, delivered, resets = FakeLoop(), [], []
    bridge = listener.SignalBridge(loop, lambda *v: delivered.append(v), lambda: resets.append(True), limit=2)
    first, second = uuid4(), uuid4()
    for _ in range(10000):
        bridge.put(first, "chat")
    bridge.put(first, "notifications")
    bridge.put(second, "chat")
    bridge.put(uuid4(), "chat")
    assert len(loop.calls) == 1 and len(bridge.pending) == 2
    loop.flush()
    assert delivered == [(first, {"chat", "notifications"}), (second, {"chat"})]
    assert resets == [True]
    bridge.close()
    bridge.put(uuid4(), "chat")
    assert not loop.calls and not bridge.pending


def test_socket_buffer_coalesces_both_domains_and_keeps_ready():
    sub = hub_module.Subscription((uuid4(), uuid4()))
    sub.offer(dict(type="ready", domains=["chat"], threads={}))
    for _ in range(10000):
        sub.offer(dict(type="changed", domains=["notifications"], threads={}))
    assert sub.frames.qsize() == 1
    frame = sub.frames.get_nowait()
    assert frame["type"] == "ready" and frame["resync"]
    assert frame["domains"] == ["chat", "notifications"]
    sub.offer(dict(type="changed", domains=["chat"], threads={}))
    sub.offer(dict(type="close", code=1008))
    sub.offer(dict(type="changed", domains=["chat"], threads={}))
    assert sub.frames.get_nowait() == dict(type="close", code=1008)


def test_owner_scoping_and_capacity_release():
    hub = hub_module.Hub(max_sockets=2)
    first, other = uuid4(), uuid4()
    one = hub.subscribe((first, uuid4()))
    two = hub.subscribe((other, uuid4()))
    one.frames.get_nowait(); two.frames.get_nowait()
    assert hub.subscribe((first, uuid4())) is None
    hub.invalidate(first, {"chat"})
    assert one.frames.qsize() == 1 and two.frames.empty()
    hub.unsubscribe(one)
    assert first not in hub.owners
    assert hub.subscribe((first, uuid4())) is not None


def test_one_auth_batch_for_100_sessions_and_revocation_without_ping():
    async def scenario():
        calls, keep = [], set()
        def validate(identities):
            calls.append(set(identities))
            return set(keep)
        hub = hub_module.Hub(validator=validate, auth_interval=0.03, resync_interval=1)
        owner = uuid4()
        subscriptions = [hub.subscribe((owner, uuid4())) for _ in range(100)]
        keep.update(sub.identity for sub in subscriptions)
        for sub in subscriptions:
            sub.frames.get_nowait()
        task = asyncio.create_task(hub.run())
        await asyncio.sleep(0.01)
        assert len(calls) == 1 and len(calls[0]) == 100
        keep.remove(subscriptions[0].identity)
        await asyncio.sleep(0.035)
        assert subscriptions[0].frames.get_nowait()["code"] == 1008
        assert len(hub.subscriptions) == 99
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_no_subscriber_no_db_and_lifespan_does_not_open_listener():
    async def scenario():
        def forbidden(*args):
            raise AssertionError("must not connect/query")
        hub = hub_module.Hub(validator=forbidden, connect=forbidden)
        await hub.start()
        assert hub.listener is None and hub.task is None
        await hub.stop()
    asyncio.run(scenario())


def test_lazy_concurrent_start_is_singleton_and_stop_closes_joined_thread():
    async def scenario():
        connected, closed = [], []
        class Connection:
            def __enter__(self):
                connected.append(True)
                return self
            def __exit__(self, *args):
                closed.append(True)
            def execute(self, statement):
                pass
            def notifies(self, **kwargs):
                # Simulate the bounded socket read without touching a network.
                threading.Event().wait(0.005)
                yield from ()
        hub = hub_module.Hub(connect=Connection)
        await hub.start()
        await asyncio.gather(*(hub.ensure_started() for _ in range(20)))
        assert len(connected) == 1
        await hub.stop()
        assert closed == [True] and not hub.listener.thread.is_alive()
        assert hub.task.done()
    asyncio.run(scenario())


def test_slow_sender_times_out_and_unsubscribes_and_cancels_receiver():
    class Socket:
        async def send_json(self, value):
            await asyncio.Event().wait()
        async def receive(self):
            await asyncio.Event().wait()
        async def close(self, **kwargs):
            pass
    async def scenario():
        hub = hub_module.Hub()
        sub = hub.subscribe((uuid4(), uuid4()))
        await asyncio.wait_for(socket_module.serve(Socket(), hub, sub), 3)
        assert not hub.subscriptions and not hub.owners
    asyncio.run(scenario())


def test_shutdown_waits_for_inflight_auth_thread_db_release():
    async def scenario():
        entered, finished = threading.Event(), threading.Event()
        def validate(identities):
            entered.set()
            threading.Event().wait(0.05)
            finished.set()
            return identities
        hub = hub_module.Hub(validator=validate)
        await hub.start()
        hub.subscribe((uuid4(), uuid4()))
        hub.task = asyncio.create_task(hub.run())
        while not entered.is_set():
            await asyncio.sleep(0.001)
        await hub.stop()
        assert finished.is_set() and hub.task.done()
    asyncio.run(scenario())


def test_socket_request_cancellation_cleans_children_without_cancelled_future():
    class Socket:
        async def send_json(self, value): await asyncio.Event().wait()
        async def receive(self): await asyncio.Event().wait()
    async def scenario():
        hub=hub_module.Hub()
        sub=hub.subscribe((uuid4(),uuid4()))
        task=asyncio.create_task(socket_module.serve(Socket(),hub,sub))
        await asyncio.sleep(0.01)
        task.cancel()
        await task
        assert not hub.subscriptions
    asyncio.run(scenario())


def test_listener_closes_generator_and_connection_and_joins_thread():
    facts = []
    owner = uuid4()
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            facts.append("closed")
        def execute(self, statement):
            assert statement == "LISTEN notes_realtime_v1"
            facts.append("listen")
        def notifies(self, **kwargs):
            assert kwargs == dict(timeout=0.5, stop_after=64)
            try:
                yield types.SimpleNamespace(payload=signal.encode(owner, "chat"))
            finally:
                facts.append("generator_closed")
    def notified(*args):
        facts.append(args)
        value.stop_event.set()
    value = listener.Listener(Connection, notified, lambda state: facts.append(("ready", state)))
    value.start(); value.stop()
    assert not value.thread.is_alive()
    assert facts == ["listen", ("ready", True), (owner, "chat"), "generator_closed", "closed"]


def test_ping_flood_is_rejected_without_any_db_work():
    class Socket:
        async def receive(self):
            return {"type": "websocket.receive", "text": '{"type":"ping"}'}
    async def scenario():
        with pytest.raises(ValueError, match="frame_rate"):
            await socket_module.receive_pings(Socket())
    asyncio.run(scenario())


@pytest.mark.parametrize("frame", [dict(type="websocket.receive", text="界"*400),
    dict(type="websocket.receive", bytes=b"data")])
def test_size_limit_is_utf8_bytes_and_binary_disallowed(frame):
    class Socket:
        async def receive(self):
            return frame
    with pytest.raises(ValueError):
        asyncio.run(socket_module.text_frame(Socket()))


@pytest.mark.parametrize("value", ["", "jobs,jobs", "unknown", "jobs, ai"])
def test_roles_reject_unknown_duplicates_or_whitespace(value):
    with pytest.raises(ValueError):
        lanes.parse_roles(value)


def test_fair_kind_rotation_prevents_continuous_diagnostic_starvation():
    called, executed = [], []
    class Service:
        def claim(self, *, kinds):
            called.extend(kinds)
            for kind in kinds:
                if kind in {"diagnostic", "pdf.import"}:
                    return dict(id=uuid4(), token="synthetic", kind=kind)
    runner = lanes.Runner(("jobs",), stopping=lambda: False, execute=lambda job: executed.append(job["kind"]),
        service=Service(), log=types.SimpleNamespace(warning=lambda *args: None))
    for _ in range(12):
        assert runner.jobs()
    assert "diagnostic" in executed and "pdf.import" in executed
    assert set(called) <= set(lanes.JOB_KINDS)


def test_outbox_lane_proceeds_while_job_lane_is_blocked():
    entered, release, delivered, stopping = (threading.Event() for _ in range(4))
    class Service:
        def claim(self, **kwargs):
            return dict(id=uuid4(), token="synthetic", kind="diagnostic")
        def deliver_outbox(self):
            delivered.set()
            return 1
    def execute(job):
        entered.set(); release.wait(2)
    runner = lanes.Runner(("jobs", "outbox"), stopping=stopping.is_set, execute=execute,
        service=Service(), log=types.SimpleNamespace(warning=lambda *args: None))
    runner.start()
    try:
        assert entered.wait(1) and delivered.wait(0.5)
    finally:
        stopping.set(); release.set()
        assert runner.close() == ()
