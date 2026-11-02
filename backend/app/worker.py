import argparse
import logging
import signal
import time
import threading
from app.modules.jobs import service
from app.shared.config import settings

log = logging.getLogger("notes.worker")
stopping = False


def stop(*_):
    global stopping
    stopping = True


def execute(job):
    log.info("job_started job_id=%s", job["id"])
    if job["kind"] in {"image.process", "pdf.import", "semantic.build", "community.publish", "notification.push", "notification.test"}:
        if job["kind"] == "notification.test":
            from app.modules.notifications.service import process_test_job as process_job
        elif job["kind"] == "notification.push":
            from app.modules.notifications.service import process_job
        elif job["kind"] == "community.publish":
            from app.modules.community.publishing import process_job
        elif job["kind"] == "semantic.build":
            from app.modules.search.semantic_service import process_job
        elif job["kind"] == "pdf.import":
            from app.modules.files.pdf_service import process_job
        else:
            from app.modules.files.service import process_job

        stop_heartbeat = threading.Event()

        def renew():
            while not stop_heartbeat.wait(min(5.0, settings().job_lease_seconds / 3)):
                if stopping or not service.heartbeat(job["id"], job["token"]):
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            if not stopping:
                process_job(job)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)
        return
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
    from app.modules.ai.chat_service import tick as ai_tick

    job = service.claim()
    if job:
        try:
            execute(job)
        except Exception:
            # Never log payloads or raw exception strings from future provider adapters.
            log.warning("handler_failed job_id=%s", job["id"])
            service.fail(job["id"], job["token"])
    service.deliver_outbox()
    ai_found = ai_tick()
    from app.modules.learning.card_generation import tick as cards_tick
    cards_found = cards_tick()
    from app.modules.learning.quiz_generation import tick as quizzes_tick
    from app.modules.learning.quiz_grading import tick as gradings_tick
    quizzes_found, gradings_found = quizzes_tick(), gradings_tick()
    return bool(job) or ai_found or cards_found or quizzes_found or gradings_found


def main():
    parser = argparse.ArgumentParser()
    from app.modules.jobs.lanes import ROLES, Runner, parse_roles
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--roles", default=None, help="comma-separated jobs,ai,outbox,push,maintenance; default all")
    args = parser.parse_args()
    try:
        roles = parse_roles(args.roles) if args.roles is not None else ROLES
    except ValueError:
        parser.error("invalid or duplicate worker role")
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    runner = Runner(roles, stopping=lambda: stopping, execute=execute, service=service, log=log)
    if args.once:
        # Backwards-compatible deterministic single sweep, no lane threads.
        # Explicit --roles runs each selected role ONCE in listed order.
        try:
            if args.roles is None:
                tick()
                runner.maintenance()
            else:
                for role in roles:
                    if stopping:
                        break
                    runner.step(role)
        except Exception:
            log.warning("worker_once_failed")
            raise SystemExit(1)
        return
    # The unchanged `python -m app.worker` Compose command now USES lanes.
    runner.start()
    try:
        while not stopping:
            time.sleep(0.1)
    finally:
        pending = runner.close(timeout=5)
        if pending:
            log.warning("worker_shutdown_inflight count=%s", len(pending))


if __name__ == "__main__":
    main()
