"""Bounded worker roles, each lane has at most one in-flight operation.

No ThreadPoolExecutor queue: outbox/maintenance never sit behind diagnostics,
file work or model I/O. AI families rotate one call at a time in the AI lane.
"""
import threading
import time

ROLES = ("jobs", "ai", "outbox", "push", "maintenance")
JOB_KINDS = ("diagnostic", "image.process", "pdf.import", "semantic.build", "community.publish", "notification.test")
PUSH_KINDS = ("notification.push",)
AI_FAMILIES = ("chat", "cards", "quizzes", "grading")


class Rotation:
    def __init__(self, values):
        self.values = tuple(values)
        if not self.values:
            raise ValueError("empty_rotation")
        self.index = 0

    def next_order(self):
        index = self.index
        self.index = (index + 1) % len(self.values)
        return self.values[index:] + self.values[:index]


def parse_roles(value):
    parts = value.split(",")
    if not parts or len(set(parts)) != len(parts) or any(part not in ROLES for part in parts):
        raise ValueError("worker_roles")
    return tuple(parts)


class Runner:
    def __init__(self, roles, *, stopping, execute, service, log, idle=0.5):
        self.roles, self.stopping = roles, stopping
        self.execute, self.service, self.log, self.idle = execute, service, log, idle
        self.wake = threading.Event()
        self.threads = []
        self.job_rotation = Rotation(JOB_KINDS)
        self.ai_rotation = Rotation(AI_FAMILIES)

    def jobs(self, *, push=False):
        kinds = PUSH_KINDS if push else self.job_rotation.next_order()
        if self.stopping():
            return False
        # A single SKIP LOCKED query orders allowed kinds by this rotation,
        # avoiding six empty SELECTs per idle job-lane iteration.
        job = self.service.claim(kinds=kinds)
        if job:
            try:
                self.execute(job)
            except Exception:
                self.log.warning("handler_failed job_id=%s", job["id"])
                self.service.fail(job["id"], job["token"])
            return True
        return False

    def ai(self):
        # Round robin across ALL families on every iteration, even if one has
        # continuously queued work. Never replay unknown paid effects here.
        from app.modules.ai.chat_service import tick as chat
        from app.modules.learning.card_generation import tick as cards
        from app.modules.learning.quiz_generation import tick as quizzes
        from app.modules.learning.quiz_grading import tick as grading
        functions = dict(chat=chat, cards=cards, quizzes=quizzes, grading=grading)
        for family in self.ai_rotation.next_order():
            if self.stopping():
                return False
            if functions[family]():
                return True
        return False

    def maintenance(self):
        from app.modules.files.service import maintenance as files
        from app.modules.learning.reminder_service import dispatch
        self.service.maintenance()
        files()
        dispatch()
        return False

    def step(self, role):
        if role == "jobs":
            return self.jobs()
        if role == "push":
            return self.jobs(push=True)
        if role == "ai":
            return self.ai()
        if role == "outbox":
            return self.service.deliver_outbox() > 0
        if role == "maintenance":
            return self.maintenance()
        raise ValueError("worker_role")

    def run_lane(self, role):
        while not self.stopping() and not self.wake.is_set():
            try:
                found = self.step(role)
            except Exception:
                self.log.warning("worker_lane_failed role=%s", role)
                found = False
            # A persistent backlog cannot become a busy unbounded claim loop.
            self.wake.wait(60 if role == "maintenance" else (0.01 if found else (0.25 if role == "outbox" else self.idle)))

    def start(self):
        for role in self.roles:
            thread = threading.Thread(target=self.run_lane, args=(role,), name="notes-worker-" + role, daemon=True)
            self.threads.append(thread)
            thread.start()

    def close(self, timeout=5):
        self.wake.set()
        deadline = time.monotonic() + timeout
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        # In-flight bounded network/file operations may not be interruptible.
        # Main process exits after grace; heartbeats stop and leases recover.
        # Existing AI unknown-outcome fencing is retained, never retried here.
        return tuple(thread.name for thread in self.threads if thread.is_alive())
