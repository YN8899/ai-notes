import os
import subprocess
import sys
import time
import threading
import queue
from app.shared.db import engine
from conftest import login, mutate


def test_killed_worker_recovers_without_duplicate_notification(client):
    login(client)
    response = mutate(client, 'POST', '/jobs/diagnostic', {'request_key': 'process-recovery', 'duration_seconds': 3})
    assert response.status_code == 202
    job_id = response.json()['id']
    env = {**os.environ, 'DATABASE_URL': os.environ['TEST_DATABASE_URL'], 'JOB_LEASE_SECONDS': '2'}
    # PGlite has one backend. Sequential connections also make the kill point
    # deterministic; separate tests cover native PostgreSQL multi-worker claims.
    engine.dispose()
    process = subprocess.Popen([sys.executable, '-m', 'app.worker', '--once'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    signals = queue.Queue()
    def collect():
        for line in process.stderr:
            signals.put(line)
    threading.Thread(target=collect, daemon=True).start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = signals.get(timeout=max(.1, deadline-time.monotonic()))
            if 'job_started' in line:
                break
            assert process.poll() is None
            time.sleep(.1)
        else:
            raise AssertionError('worker did not claim the job')
        process.kill()
        process.wait(timeout=5)
        row = client.get('/api/v1/jobs/' + job_id).json()
        assert row['status'] == 'running'
        assert row['attempts'] == 1
        time.sleep(2.2)
        engine.dispose()
        subprocess.run([sys.executable, '-m', 'app.worker', '--once'], env=env, check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        row = client.get('/api/v1/jobs/' + job_id).json()
        assert row['status'] == 'succeeded'
        assert row['attempts'] == 2
        assert len(client.get('/api/v1/notifications').json()) == 1
        engine.dispose()
        subprocess.run([sys.executable, '-m', 'app.worker', '--once'], env=env, check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert len(client.get('/api/v1/notifications').json()) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
