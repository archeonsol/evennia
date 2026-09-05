"""Opt-in Redis maintenance and job transition tests against a private instance."""

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

import redis
from django.test import SimpleTestCase, override_settings

from evennia.jobs import queue


@skipUnless(os.environ.get("EVENNIA_REDIS_SERVER"), "Set EVENNIA_REDIS_SERVER for real Redis tests")
class TestRealRedisCutover(SimpleTestCase):
    """Exercise production commands without touching a configured Redis service."""

    def setUp(self):
        """Own a private Unix socket, process, log, and namespace per test."""
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="bus-cutover-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        socket = root / "redis.sock"
        config = root / "redis.conf"
        config.write_text(
            f'port 0\nunixsocket {socket}\nunixsocketperm 700\nsave ""\nappendonly no\n'
        )
        self.log = (root / "redis.log").open("wb")
        self.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            [os.environ["EVENNIA_REDIS_SERVER"], str(config)],
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.stop_process)
        self.url = f"unix://{socket}?db=0"
        self.client = redis.Redis.from_url(self.url, socket_timeout=1, socket_connect_timeout=1)
        self.addCleanup(self.client.close)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.client.ping()
                break
            except redis.ConnectionError:
                if self.process.poll() is not None:
                    self.fail((root / "redis.log").read_text())
                time.sleep(0.02)
        else:
            self.fail("Private Redis did not become available")
        self.prefix = f"validation:{uuid4().hex}"
        self.repo = Path(__file__).resolve().parents[3]

    def stop_process(self):
        """Clean up without using the Redis connection under test."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def maintenance(self, mode):
        """Execute the exact operator block with this test's explicit scope."""
        document = self.repo / "docs/source/Components/Redis-Bus-Cutover.md"
        source = document.read_text().split("```python\n", 1)[1].split("```", 1)[0]
        with patch.dict(
            os.environ,
            {
                "REDIS_BUS_URL": self.url,
                "REDIS_BUS_PREFIX": self.prefix,
                "REDIS_BUS_WORKERS": "0,other",
                "REDIS_BUS_MAINTENANCE": mode,
                "REDIS_BUS_PEERS_STOPPED": "yes",
            },
        ):
            exec(compile(source, str(document), "exec"), {})

    def seed(self):
        """Create old pending and undelivered actions plus unrelated state."""
        self.streams = [f"{self.prefix}:s2p", f"{self.prefix}:p2s:0", f"{self.prefix}:p2s:other"]
        self.sentinel = f"{self.prefix}:unrelated"
        self.client.set(self.sentinel, b"preserve")
        for stream in self.streams:
            self.client.xadd(stream, {b"c": b"Msg", b"d": b"pending"})
            self.client.xgroup_create(stream, f"{stream}:grp", id="0")
            self.client.xreadgroup(f"{stream}:grp", "old-pid", {stream: ">"}, count=1)
            self.client.xadd(stream, {b"c": b"Msg", b"d": b"retained"})
            self.client.xgroup_create(stream, "unrelated-group", id="0")
            self.client.xreadgroup("unrelated-group", "observer", {stream: ">"}, count=1)

    def assert_preserved(self):
        """Check stream payloads and unrelated group pending state independently."""
        self.assertEqual(self.client.get(self.sentinel), b"preserve")
        for stream in self.streams:
            self.assertEqual(self.client.xlen(stream), 2)
            self.assertEqual(self.client.xpending(stream, "unrelated-group")["pending"], 1)

    def test_upgrade_preserves_payloads_and_unrelated_groups(self):
        """Only obsolete expected groups are removed during upgrade."""
        self.seed()
        self.maintenance("upgrade")
        self.maintenance("upgrade")
        self.assert_preserved()
        for stream in self.streams:
            self.assertEqual(
                [group["name"] for group in self.client.xinfo_groups(stream)], [b"unrelated-group"]
            )

    def test_wrong_key_type_aborts_before_destroying_any_group(self):
        """A configuration mistake cannot partially remove the expected groups."""
        self.seed()
        self.client.delete(self.streams[-1])
        self.client.set(self.streams[-1], b"not a stream")
        with self.assertRaisesRegex(RuntimeError, "Expected a stream"):
            self.maintenance("upgrade")
        for stream in self.streams[:-1]:
            self.assertEqual(self.client.xpending(stream, f"{stream}:grp")["pending"], 1)

    def test_cleanup_kills_an_unresponsive_owned_process(self):
        """Cleanup cannot depend on Redis answering a shutdown request."""
        os.kill(self.process.pid, signal.SIGSTOP)
        started = time.monotonic()
        self.stop_process()
        self.assertLess(time.monotonic() - started, 4.5)
        self.assertEqual(self.process.returncode, -signal.SIGKILL)

    def test_rollback_old_reader_skips_pending_and_retained_actions(self):
        """The previous release can read new work without replaying retained work."""
        self.seed()
        self.maintenance("rollback")
        self.maintenance("rollback")
        self.assert_preserved()
        source = subprocess.run(
            ["git", "show", "underspire.227:evennia/server/redis_bus.py"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        legacy = types.ModuleType("legacy_redis_bus_validation")
        exec(compile(source, "underspire.227/redis_bus.py", "exec"), legacy.__dict__)
        for stream in self.streams:
            with self.subTest(stream=stream):
                delivered = []
                transport = legacy._RedisTransport(stream, lambda *args: delivered.append(args))
                transport._client = self.client
                transport._ensure_group()
                with patch.object(
                    legacy.clock, "call_from_thread", side_effect=lambda fn, *args: fn(*args)
                ):
                    transport._drain_pending()
                    self.assertEqual(delivered, [])
                    self.assertEqual(
                        self.client.xreadgroup(
                            transport._group, transport._consumer, {stream: ">"}, count=64
                        ),
                        [],
                    )
                    self.client.xadd(stream, {b"c": b"Msg", b"d": b"fresh"})
                    dispatch = transport._dispatch

                    def stop_after_dispatch(*args):
                        dispatch(*args)
                        transport._stop.set()

                    transport._dispatch = stop_after_dispatch
                    reader = threading.Thread(target=transport._reader_loop, daemon=True)
                    reader.start()
                    try:
                        reader.join(timeout=2)
                        self.assertFalse(reader.is_alive(), "Old reader did not deliver fresh work")
                    finally:
                        transport._stop.set()
                        reader.join(timeout=2)
                self.assertEqual(delivered, [(b"Msg", b"fresh")])
                self.assertEqual(self.client.xpending(stream, transport._group)["pending"], 0)

    def test_job_committed_reply_loss_leaves_one_replacement(self):
        """Retrying the production Lua transition after reply loss is a no-op."""
        observer = redis.Redis.from_url(self.url, socket_timeout=1)
        self.addCleanup(observer.close)
        for attempts in (1, 3):
            with (
                self.subTest(attempts=attempts),
                override_settings(
                    JOB_QUEUE_REDIS_KEY=f"{self.prefix}:jobs:{attempts}", SERVER_WORKER_ID="0"
                ),
            ):
                pending, processing, dead = queue._redis_keys()
                raw = json.dumps(
                    {"id": "job", "type": "test", "attempts": attempts - 1, "max_attempts": 3}
                )
                record = {**json.loads(raw), "attempts": attempts, "_raw": raw}
                self.client.lpush(processing, raw)
                evaluate = self.client.eval

                def lost_reply(*args):
                    evaluate(*args)
                    raise redis.ConnectionError("committed Lua reply lost")

                with patch("django_redis.get_redis_connection", return_value=self.client):
                    with (
                        patch.object(self.client, "eval", side_effect=lost_reply),
                        patch.object(queue.logger, "log_trace") as log,
                    ):
                        queue._retry_or_dead_redis(record)
                    log.assert_called_once_with("job_queue: redis retry/dead failed")
                    queue._retry_or_dead_redis(record)
                destination = dead if attempts == 3 else pending
                other = pending if attempts == 3 else dead
                self.assertEqual(observer.llen(processing), 0)
                self.assertEqual(observer.llen(destination), 1)
                self.assertEqual(observer.llen(other), 0)
                self.assertEqual(json.loads(observer.lindex(destination, 0))["attempts"], attempts)
