"""Process-local owner fanout; PostgreSQL facts stay in HTTP APIs.

One process LISTEN connection, one auth batch per second, one coalesced frame
per socket. No per-socket SQL, thread list polling, or SQL on incoming pings.
"""
import asyncio
from contextlib import suppress
import time
from .realtime_listener import Listener, SignalBridge

DOMAINS = frozenset({"chat", "notifications"})
AUTH_CHECKOUT_SECONDS = 0.1
AUTH_CONNECT_SECONDS = 2
AUTH_STATEMENT_MS = 800
AUTH_SHUTDOWN_SECONDS = 4


class IdentityValidator:
    """One owned auth connection, independent of the application QueuePool.

    A raw DBAPI pool avoids pre_ping/dialect initialization queries before the
    auth SELECT. Cold work has finite checkout/connect/statement budgets; the
    single Hub caller never queues concurrent validation operations.
    """
    def __init__(self):
        self.pool = None
        self.closed = False
        self.calls = 0

    def __call__(self, identities):
        identities = set(identities)
        if self.closed:
            raise RuntimeError("realtime_auth_closed")
        if not identities:
            return set()
        import psycopg
        from datetime import datetime, timezone
        if self.pool is None:
            from sqlalchemy.pool import QueuePool
            from sqlalchemy.engine import make_url
            from app.shared.config import settings
            url = make_url(settings().database_url).set(drivername="postgresql")
            def connect():
                return psycopg.connect(url.render_as_string(hide_password=False), autocommit=True,
                    connect_timeout=AUTH_CONNECT_SECONDS, application_name="notes-realtime-auth",
                    options=f"-c statement_timeout={AUTH_STATEMENT_MS}ms -c lock_timeout={AUTH_STATEMENT_MS}ms")
            self.pool = QueuePool(connect, pool_size=1, max_overflow=0,
                timeout=AUTH_CHECKOUT_SECONDS, reset_on_return=None)
        connection = self.pool.connect()
        try:
            self.calls += 1
            rows = connection.execute("""SELECT s.user_id,s.id FROM sessions s
                JOIN users u ON u.id=s.user_id
                WHERE s.id=ANY(%s::uuid[]) AND u.status='active'
                  AND s.revoked_at IS NULL AND s.expires_at>%s""",
                (list({session for _, session in identities}), datetime.now(timezone.utc))).fetchall()
            return set(rows) & identities
        except (psycopg.OperationalError, psycopg.InterfaceError):
            connection.invalidate()
            raise
        finally:
            connection.close()

    def close(self):
        self.closed = True
        if self.pool is not None:
            self.pool.dispose()


class Subscription:
    def __init__(self, identity):
        self.identity = identity
        self.frames = asyncio.Queue(maxsize=1)
        self.closed = False

    def offer(self, frame):
        if self.closed:
            return
        if frame["type"] == "close":
            self.closed = True
        if self.frames.full():
            previous = self.frames.get_nowait()
            if previous["type"] == "close":
                frame = previous
            elif frame["type"] != "close":
                # Coalesce invalidations rather than dropping changes. HTTP
                # catch-up is mandatory on ready, reconnect and every resync.
                frame = dict(frame, type="ready" if previous["type"] == "ready" else frame["type"],
                    domains=sorted(set(previous.get("domains", [])) | set(frame.get("domains", []))), resync=True)
        self.frames.put_nowait(frame)


def validate_identities(identities):
    # Public one-shot helper. Hub owns/reuses its own validator until shutdown.
    validator = IdentityValidator()
    try:
        return validator(identities)
    finally:
        validator.close()


def connect_listener():
    import psycopg
    from sqlalchemy.engine import make_url
    from app.shared.config import settings
    url = make_url(settings().database_url).set(drivername="postgresql")
    return psycopg.connect(url.render_as_string(hide_password=False), autocommit=True,
        connect_timeout=3, application_name="notes-realtime-listener", options="-c statement_timeout=3000")


class Hub:
    def __init__(self, *, validator=None, connect=connect_listener,
                 auth_interval=1.0, resync_interval=15.0, max_sockets=1024, max_owner_sockets=256):
        self.owned_validator = IdentityValidator() if validator is None else None
        self.validator = self.owned_validator if validator is None else validator
        self.connect = connect
        self.auth_interval, self.resync_interval = auth_interval, resync_interval
        self.max_sockets, self.max_owner_sockets = max_sockets, max_owner_sockets
        self.owners, self.subscriptions = {}, set()
        self.listener = self.bridge = self.task = None
        self.validation = None
        self.listening = False
        self.closed = False

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.start_lock = asyncio.Lock()

    async def ensure_started(self):
        async with self.start_lock:
            if self.listener is not None:
                return
            await self._start_listener()

    async def _start_listener(self):
        self.established = asyncio.Event()
        self.bridge = SignalBridge(self.loop, self.invalidate, self.resync)
        self.listener = Listener(self.connect, self.bridge.put, self.listener_state)
        self.listener.start()
        self.task = asyncio.create_task(self.run(), name="notes-realtime-auth")
        # Startup remains available with durable HTTP if LISTEN cannot connect.
        # Normal startup establishes/commits LISTEN before sockets may subscribe.
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.established.wait(), 3.5)

    def listener_state(self, connected):
        # Called once per connection transition, not once per notification.
        try:
            self.loop.call_soon_threadsafe(self._listener_state, connected)
        except RuntimeError:
            pass

    def _listener_state(self, connected):
        if self.closed:
            return
        self.listening = connected
        if connected:
            self.established.set()
        self.resync()

    def subscribe(self, identity):
        owner, _ = identity
        existing = self.owners.get(owner, set())
        if self.closed or len(self.subscriptions) >= self.max_sockets or len(existing) >= self.max_owner_sockets:
            return None
        sub = Subscription(identity)
        self.subscriptions.add(sub)
        self.owners.setdefault(owner, set()).add(sub)
        # Clients take authoritative HTTP snapshots AFTER registration. Signals
        # committed during HTTP reads are queued and force another catch-up.
        sub.offer(self.frame("ready", DOMAINS, resync=True))
        return sub

    def unsubscribe(self, sub):
        self.subscriptions.discard(sub)
        items = self.owners.get(sub.identity[0])
        if items is not None:
            items.discard(sub)
            if not items:
                del self.owners[sub.identity[0]]
        sub.closed = True

    def frame(self, kind, domains=(), *, resync=False):
        # Empty legacy threads mapping remains valid: clients invalidate/refetch
        # on type, while HTTP after_seq is the only cursor/data authority.
        return dict(type=kind, threads={}, domains=sorted(domains), resync=resync, realtime=self.listening)

    def invalidate(self, owner, domains):
        if self.closed:
            return
        for sub in tuple(self.owners.get(owner, ())):
            sub.offer(self.frame("changed", set(domains) & DOMAINS))

    def resync(self):
        if self.closed:
            return
        for sub in tuple(self.subscriptions):
            sub.offer(self.frame("changed", DOMAINS, resync=True))

    async def run(self):
        last_resync = time.monotonic()
        while True:
            start = time.monotonic()
            batch = tuple(self.subscriptions)
            if batch:
                try:
                    self.validation = asyncio.create_task(asyncio.to_thread(self.validator, {sub.identity for sub in batch}))
                    # Cancelling the hub does not stop an already running SQL
                    # thread. Keep its future so shutdown can await DB release.
                    valid = await asyncio.shield(self.validation)
                except Exception:
                    valid = set()  # Fail closed; never emit DB/error details.
                self.validation = None
                for sub in batch:
                    if sub.identity not in valid:
                        sub.offer({"type": "close", "code": 1008})
                        self.unsubscribe(sub)
            if time.monotonic() - last_resync >= self.resync_interval:
                self.resync()
                last_resync = time.monotonic()
            # One process-wide batch every second; pings never wake this task.
            await asyncio.sleep(max(0.01, self.auth_interval - (time.monotonic() - start)))

    async def stop(self):
        self.closed = True
        for sub in tuple(self.subscriptions):
            sub.offer({"type": "close", "code": 1012})
            self.unsubscribe(sub)
        if self.bridge:
            self.bridge.close()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        try:
            if self.validation:
                try:
                    await asyncio.wait_for(asyncio.shield(self.validation), AUTH_SHUTDOWN_SECONDS)
                except asyncio.TimeoutError:
                    raise RuntimeError("realtime_auth_shutdown_timeout") from None
                except Exception:
                    pass  # Failed auth query already closed its context; no raw DB exception.
        finally:
            if self.owned_validator:
                self.owned_validator.close()
            if self.listener:
                await asyncio.to_thread(self.listener.stop)
