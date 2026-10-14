import argparse
import logging
import signal
import time
from app.modules.jobs import service
from app.shared.config import settings

log = logging.getLogger("notes.worker")
stopping = False


def stop(*_):
    global stopping
    stopping = True


def execute(job):
    log.info("job_started job_id=%s", job["id"])
    if job["kind"] != "diagnostic":
        raise ValueError("unsupported_handler")
    deadline = time.monotonic() + job["payload"]["duration_seconds"]
    interval = min(1.0, settings().job_lease_seconds / 3)
    while time.monotonic() < deadline:
        if stopping:
            return  # Let the lease expire so another worker can recover it.
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        if not service.heartbeat(job["id"], job["token"]):
            return
    service.finish(job["id"], job["token"], {"ok": True})


def tick():
    job = service.claim()
    if job:
        try:
            execute(job)
        except Exception:
            # Never log payloads or raw exception strings from future provider adapters.
            log.warning("handler_failed job_id=%s", job["id"])
            service.fail(job["id"], job["token"])
    service.deliver_outbox()
    return bool(job)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_maintenance = 0.0
    while not stopping:
        try:
            found = tick()
            if time.monotonic() - last_maintenance > 3600:
                service.maintenance()
                last_maintenance = time.monotonic()
        except Exception:
            log.warning("worker_tick_failed")
            if args.once:
                raise SystemExit(1)
            found = False
        if args.once:
            break
        if not found:
            time.sleep(1)


if __name__ == "__main__":
    main()
