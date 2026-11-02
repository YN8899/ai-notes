"""Best-effort generic push alongside the durable in-app notification fact."""
import base64
import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import webpush, WebPushException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.shared import outbound
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import AuthSession, Job, Notification, Outbox, User
from app.shared.security import digest
from .models import PushSubscription
from .schemas import decode_key, public_key

MESSAGE = "知页有新的站内提醒"
PUSH_HOSTS = frozenset({"fcm.googleapis.com", "web.push.apple.com", "updates.push.services.mozilla.com"})
PUSH_SUFFIXES = (".notify.windows.com",)


def public_vapid():
    return settings().push_vapid_public_key


def vapid():
    cfg = settings()
    if not cfg.push_enabled or not cfg.push_vapid_subject:
        return None
    try:
        public_key(cfg.push_vapid_public_key)
        private = ec.derive_private_key(int.from_bytes(decode_key(cfg.push_vapid_private_key.get_secret_value(), 32), "big"), ec.SECP256R1())
        actual = base64.urlsafe_b64encode(private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).decode().rstrip("=")
        if actual != cfg.push_vapid_public_key:
            return None
        subject = cfg.push_vapid_subject
        if not (subject.startswith("mailto:") and "@" in subject and all(32 < ord(c) < 127 for c in subject)):
            return None
        return Vapid(private)
    except (ValueError, TypeError):
        return None


def validate_endpoint(endpoint, *, resolve=True):
    try:
        target = outbound.validate_url(endpoint)
        if target.host not in PUSH_HOSTS and not any(target.host.endswith(suffix) for suffix in PUSH_SUFFIXES):
            raise outbound.UnsafeDestination()
        if resolve:
            outbound.resolve_public(target.host)
        return target
    except outbound.UnsafeDestination:
        raise AppError(422, "push_endpoint_invalid", "浏览器推送地址不受支持") from None
    except outbound.EndpointUnavailable:
        raise AppError(503, "push_endpoint_unavailable", "推送服务地址暂不可用，请稍后重试") from None


def enqueue_notification(db, notification):
    """Called in the same transaction as notification persistence; no external I/O."""
    if vapid() is None:
        return
    now = utcnow()
    query = select(PushSubscription).join(AuthSession, AuthSession.id == PushSubscription.session_id)
    # Test reminders must never fan out to the account's other devices. The
    # durable event carries only immutable registration/session IDs, no keys.
    event = db.get(Outbox, notification.event_id)
    if event and event.kind == "notification.test":
        try:
            target, session = UUID(event.payload["subscription_id"]), UUID(event.payload["session_id"])
        except (ValueError, TypeError, KeyError):
            return
        query = query.where(PushSubscription.id == target, PushSubscription.session_id == session)
    rows = db.scalars(query
        .join(User, User.id == PushSubscription.owner_id).where(PushSubscription.owner_id == notification.user_id,
            AuthSession.user_id == notification.user_id, User.status == "active", PushSubscription.enabled.is_(True),
            PushSubscription.vapid_key_hash == digest(public_vapid()), AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)).all()
    for row in rows:
        dedupe = hashlib.sha256(f"notification.push:{row.id}:{notification.id}".encode()).hexdigest()
        db.execute(insert(Job).values(id=uuid4(), owner_id=notification.user_id, kind="notification.push", dedupe_key=dedupe,
            payload={"subscription_id": str(row.id), "notification_id": str(notification.id)}, status="queued", attempts=0,
            max_attempts=3, run_after=now, created_at=now).on_conflict_do_nothing(constraint="job_dedupe"))


def enqueue_test(db, owner_id, session_id, data):
    if vapid() is None:
        raise AppError(503, "push_disabled", "浏览器推送尚未配置，站内提醒仍可使用")
    row = db.scalar(select(PushSubscription).where(PushSubscription.id == data.subscription_id,
        PushSubscription.owner_id == owner_id, PushSubscription.session_id == session_id).with_for_update())
    if not row:
        raise AppError(404, "not_found", "此会话的订阅不存在")
    if not row.enabled or row.vapid_key_hash != digest(public_vapid()):
        raise AppError(409, "push_unavailable", "此会话的推送已失效，请重新关联本设备")
    payload = {"subscription_id": str(row.id), "session_id": str(session_id)}
    # Share the existing owner-wide request namespace, detecting collisions
    # with diagnostic/other jobs as well as changed subscription parameters.
    request_key = str(data.request_key)
    existing = db.scalar(select(Job).where(Job.owner_id == owner_id, Job.dedupe_key == request_key))
    if existing:
        if existing.kind != "notification.test" or existing.payload != payload:
            raise AppError(409, "request_key_conflict", "同一个请求标识不能用于不同任务")
        return existing
    active = db.scalars(select(Job.id).where(Job.owner_id == owner_id, Job.status.in_(["queued", "running"])).limit(10)).all()
    if len(active) >= 10:
        raise AppError(429, "queue_full", "在途任务已达上限，请稍后再试")
    now = utcnow()
    job = Job(owner_id=owner_id, kind="notification.test", dedupe_key=request_key, payload=payload,
        status="queued", attempts=0, max_attempts=3, run_after=now)
    db.add(job)
    db.flush()
    return job


def process_test_job(job):
    """Lease-fenced persistent test notification; no external I/O here."""
    if not lease_current(job["id"], job["token"]):
        return False
    with SessionLocal.begin() as db:
        stored = db.get(Job, job["id"])
        try:
            if stored.kind != "notification.test" or set(stored.payload) != {"subscription_id", "session_id"}:
                raise ValueError()
            subscription_id, session_id = UUID(stored.payload["subscription_id"]), UUID(stored.payload["session_id"])
        except (ValueError, TypeError, KeyError):
            return complete(job["id"], job["token"], outcome="invalid")
        user = db.scalar(select(User).where(User.id == stored.owner_id).with_for_update(read=True))
        session = db.scalar(select(AuthSession).where(AuthSession.id == session_id).with_for_update(read=True))
        subscription = db.scalar(select(PushSubscription).where(PushSubscription.id == subscription_id).with_for_update())
        valid = (vapid() is not None and user and user.status == "active" and session
            and session.user_id == stored.owner_id and session.revoked_at is None and session.expires_at > utcnow()
            and subscription and subscription.owner_id == stored.owner_id and subscription.session_id == session_id
            and subscription.enabled and subscription.vapid_key_hash == digest(public_vapid()))
        row = db.scalar(select(Job).where(Job.id == job["id"]).with_for_update().execution_options(populate_existing=True))
        if not row or row.status != "running" or row.worker_token != job["token"] or row.lease_until <= utcnow():
            return False
        if valid:
            db.execute(insert(Outbox).values(id=uuid4(), user_id=row.owner_id, job_id=row.id, kind="notification.test",
                payload={"message": "知页浏览器提醒测试：请以站内提醒记录为准", **row.payload}, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=[Outbox.job_id]))
        finish_row(row, "queued" if valid else "skipped", False, None)
    return True


class PushHttp:
    def __init__(self, check):
        self.check = check

    def post(self, endpoint, *, data, headers, timeout):
        validate_endpoint(endpoint, resolve=False)
        # Outbound runs this gate AFTER DNS/encryption/transport preparation,
        # immediately before handle_request; checking here leaves a DNS window.
        response = outbound.post(endpoint, data=data, headers=headers, timeout=timeout or 10.0, before_send=self.check)
        return SimpleNamespace(status_code=response.status_code, reason="", text="", headers={})


def current_day_check(db, event):
    """Preload immutable digest metadata; the returned predicate does no I/O."""
    if event is None:
        return lambda now: False
    if event.kind != "review.daily":
        return lambda now: True
    from app.modules.learning.reminder_models import DailyDigest
    row = db.scalar(select(DailyDigest).where(DailyDigest.job_id == event.job_id, DailyDigest.owner_id == event.user_id))
    try:
        if row is None:
            return lambda now: False
        local_date, zone = row.local_date, ZoneInfo(row.timezone)
    except (ValueError, KeyError):
        return lambda now: False
    return lambda now: now.astimezone(zone).date() == local_date


def current_lease_until(job_id, token):
    with SessionLocal() as db:
        return db.scalar(select(Job.lease_until).where(Job.id == job_id, Job.status == "running", Job.worker_token == token))


def lease_current(job_id, token):
    expires = current_lease_until(job_id, token)
    # A database roundtrip must not make a naturally elapsed lease current.
    return expires is not None and expires > utcnow()


def complete(job_id, token, *, outcome, retry=False, code=None):
    with SessionLocal.begin() as db:
        row = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not row or row.status != "running" or row.worker_token != token or row.lease_until <= utcnow():
            return False
        finish_row(row, outcome, retry, code)
        return True


def finish_row(row, outcome, retry, code):
    from datetime import timedelta
    row.status = ("queued" if row.attempts < row.max_attempts else "failed") if retry else "succeeded"
    row.result = {"outcome": outcome}
    row.error_code = code
    if retry:
        row.run_after = utcnow() + timedelta(seconds=min(300, 2 ** row.attempts))
    row.worker_token, row.lease_until = None, None


def process_job(job):
    if not lease_current(job["id"], job["token"]):
        return False
    outcome, retry, code, disable = "skipped", False, None, False
    with SessionLocal.begin() as db:
        stored = db.get(Job, job["id"])
        # Treat job payload as untrusted, including manually restored queue rows.
        try:
            if stored.kind != "notification.push" or set(stored.payload) != {"subscription_id", "notification_id"}:
                raise ValueError()
            subscription_id, notification_id = UUID(stored.payload["subscription_id"]), UUID(stored.payload["notification_id"])
        except (ValueError, TypeError, KeyError):
            return complete(job["id"], job["token"], outcome="invalid")
        # Lock in the same user -> session -> subscription order as auth and API.
        # Revocation/disable/delete cannot commit between these checks and send.
        user = db.scalar(select(User).where(User.id == stored.owner_id).with_for_update(read=True))
        subscription = db.get(PushSubscription, subscription_id)
        session_id = subscription.session_id if subscription else None
        session = db.scalar(select(AuthSession).where(AuthSession.id == session_id).with_for_update(read=True)) if session_id else None
        subscription = db.scalar(select(PushSubscription).where(PushSubscription.id == subscription_id)
            .with_for_update().execution_options(populate_existing=True))
        notice = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update(read=True))
        event = db.get(Outbox, notice.event_id) if notice else None
        day_current = current_day_check(db, event)
        key = vapid()
        def eligible(now, configured):
            # These rows remain locked through the send, preventing revocation,
            # rebind/read/disable commits. Time and configuration can change
            # during DNS, so re-evaluate both in the final transport gate.
            valid = (configured and user and user.status == "active" and subscription and subscription.enabled
                and subscription.id == subscription_id and subscription.session_id == session_id
                and subscription.owner_id == stored.owner_id and subscription.vapid_key_hash == digest(public_vapid())
                and session and session.user_id == stored.owner_id and session.revoked_at is None and session.expires_at > now
                and notice and notice.id == notification_id and notice.user_id == stored.owner_id and notice.read_at is None
                and event and event.user_id == stored.owner_id and day_current(now))
            if valid and event.kind == "notification.test":
                valid = (event.payload.get("subscription_id") == str(subscription_id)
                    and event.payload.get("session_id") == str(session_id))
            return bool(valid)
        valid = eligible(utcnow(), key is not None)
        if valid:
            def before_send():
                # This is the LAST possibly blocking SQL. DailyDigest metadata
                # was preloaded above, and all predicates below are pure reads.
                expires = current_lease_until(job["id"], job["token"])
                configured = vapid() is not None
                # Capture time only AFTER the SQL and VAPID CPU preparation;
                # check local midnight, session and actual lease together.
                now = utcnow()
                return expires is not None and expires > now and eligible(now, configured)

            cancelled, unavailable = False, False
            try:
                response = webpush(subscription_info={"endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}, data=MESSAGE,
                    vapid_private_key=key, vapid_claims={"sub": settings().push_vapid_subject},
                    ttl=300, timeout=10, headers={"Topic": "notes-notification"}, verbose=False,
                    requests_session=PushHttp(before_send))
                status = response.status_code
            except outbound.SendCancelled:
                status, cancelled = None, True
            except outbound.EndpointUnavailable:
                status, unavailable = None, True
            except WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
            except AppError:
                status = 400
            except Exception:
                status = None
            if cancelled:
                outcome = "skipped"
            elif unavailable:
                outcome, retry, code = "deferred", True, "push_endpoint_unavailable"
            elif status in {404, 410}:
                disable = True
                outcome = "expired"
            elif status is None or status == 429 or 500 <= status <= 599:
                outcome, retry, code = "unknown" if status is None else "deferred", True, "push_delivery_unknown" if status is None else "push_retryable"
            elif 200 <= status <= 202:
                # Accepted by the service, not proof of delivery/display exactly once.
                outcome = "accepted"
            else:
                outcome, code = "rejected", "push_rejected"
        # Fencing also covers permanent subscription disable, in the same commit.
        row = db.scalar(select(Job).where(Job.id == job["id"]).with_for_update().execution_options(populate_existing=True))
        if not row or row.status != "running" or row.worker_token != job["token"] or row.lease_until <= utcnow():
            return False
        if disable:
            subscription.enabled = False
        finish_row(row, outcome, retry, code)
    return True
