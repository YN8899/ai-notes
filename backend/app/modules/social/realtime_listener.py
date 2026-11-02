"""One sync listener thread per API process, also on Windows Proactor loops."""
import threading
from .realtime_signal import CHANNEL, decode


class Listener:
    def __init__(self, connect, notify, reset):
        self.connect, self.notify, self.reset = connect, notify, reset
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="notes-pg-listener", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("realtime_listener_shutdown_timeout")

    def run(self):
        delay = 0.25
        while not self.stop_event.is_set():
            try:
                # Factory uses connect_timeout=3, autocommit=True and a dedicated
                # connection, never an ORM/pool connection shared with queries.
                with self.connect() as conn:
                    self.backend_pid = getattr(getattr(conn, "info", None), "backend_pid", None)
                    conn.execute("LISTEN " + CHANNEL)
                    self.reset(True)  # LISTEN committed BEFORE hub snapshots.
                    delay = 0.25
                    while not self.stop_event.is_set():
                        gen = conn.notifies(timeout=0.5, stop_after=64)
                        try:
                            for value in gen:
                                signal = decode(value.payload)
                                if signal:
                                    self.notify(*signal)
                                if self.stop_event.is_set():
                                    break
                        finally:
                            gen.close()
            except Exception:
                # Never log DSNs, SQL parameters or raw exception/notify data.
                self.reset(False)
                self.stop_event.wait(delay)
                delay = min(5, delay * 2)


class SignalBridge:
    """Bound the cross-thread -> event loop backlog, including callback count."""
    def __init__(self, loop, deliver, reset, limit=4096):
        self.loop, self.deliver, self.reset, self.limit = loop, deliver, reset, limit
        self.lock = threading.Lock()
        self.pending, self.scheduled, self.overflow = {}, False, False
        self.closed = False

    def put(self, owner, kind):
        with self.lock:
            if self.closed:
                return
            if owner in self.pending or len(self.pending) < self.limit:
                self.pending.setdefault(owner, set()).add(kind)
            else:
                self.overflow = True
            if self.scheduled:
                return
            self.scheduled = True
        try:
            self.loop.call_soon_threadsafe(self.drain)
        except RuntimeError:
            self.close()

    def drain(self):
        with self.lock:
            pending, overflow = self.pending, self.overflow
            self.pending, self.overflow, self.scheduled = {}, False, False
        if self.closed:
            return
        if overflow:
            self.reset()
        for owner, kinds in pending.items():
            self.deliver(owner, kinds)

    def close(self):
        with self.lock:
            self.closed = True
            self.pending.clear()
