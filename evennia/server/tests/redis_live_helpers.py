"""Owned Redis processes and a RESP reply-loss proxy for opt-in fault tests."""

import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import redis

REDIS_SERVER = os.environ.get("EVENNIA_REDIS_SERVER")


def await_condition(predicate, seconds=5):
    """Wait against one deadline and fail instead of leaving a hung harness."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("live Redis condition did not complete before deadline")


class RedisProcess:
    """Run a disposable loopback Redis without persistence."""

    def __init__(self, executable=REDIS_SERVER):
        """Start isolated Redis and clean up resources if startup fails."""
        self.directory = tempfile.TemporaryDirectory(prefix="evennia-redis-live-")
        self.process = None
        self.client = None
        self.log = open(Path(self.directory.name) / "redis.log", "wb")
        try:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                self.port = reservation.getsockname()[1]
            self.url = f"redis://127.0.0.1:{self.port}/0"
            self.process = subprocess.Popen(
                [
                    executable,
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    self.directory.name,
                ],
                stdin=subprocess.DEVNULL,
                stdout=self.log,
                stderr=subprocess.STDOUT,
            )
            self.client = redis.Redis.from_url(
                self.url, socket_connect_timeout=0.2, socket_timeout=0.5
            )

            self._wait_ready()
        except BaseException:
            self.close()
            raise

    def _wait_ready(self):
        """Require the owned process to answer through an independent client."""

        def responding():
            """Distinguish a live startup delay from an exited Redis process."""
            if self.process.poll() is not None:
                raise AssertionError("isolated Redis exited during startup")
            try:
                return self.client.ping()
            except redis.ConnectionError:
                return False

        await_condition(responding)

    def restart(self):
        """Replace Redis at the same endpoint with empty nonpersistent state."""
        arguments = self.process.args
        self.stop()
        self.client.close()
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.client = redis.Redis.from_url(self.url, socket_connect_timeout=0.2, socket_timeout=0.5)
        self._wait_ready()

    def stop(self):
        """Terminate the owned process, escalating only that PID after a deadline."""
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    def close(self):
        """Release connections, process, log, and temporary files."""
        self.stop()
        if self.client is not None:
            self.client.close()
        self.log.close()
        self.directory.cleanup()


def _resp(stream):
    """Read one RESP2/RESP3 value while preserving its exact wire bytes."""
    line = stream.readline()
    if not line:
        raise EOFError
    if not line.endswith(b"\r\n"):
        raise ValueError("incomplete RESP header")
    marker, body = line[:1], line[1:-2]
    if marker in (b"$", b"!", b"="):
        length = int(body)
        if length < 0:
            return line, None
        payload = stream.read(length + 2)
        if len(payload) != length + 2:
            raise EOFError
        return line + payload, payload[:-2]
    if marker in (b"*", b"~", b">", b"%", b"|"):
        count = int(body)
        if count < 0:
            return line, None
        if marker in (b"%", b"|"):
            count *= 2
        children = [_resp(stream) for _ in range(count)]
        return line + b"".join(wire for wire, _ in children), [value for _, value in children]
    if marker in (b"+", b"-", b":", b",", b"#", b"_", b"("):
        return line, body
    raise ValueError(f"unsupported RESP marker {marker!r}")


class ReplyLossProxy:
    """Forward real Redis requests, then close one committed XADD reply socket."""

    def __init__(self, redis_port):
        """Start a loopback proxy targeting the owned Redis port."""
        self.redis_port = redis_port
        self.dropped = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []
        self.workers = []
        self.errors = []
        self.xadds = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.url = f"redis://127.0.0.1:{self.listener.getsockname()[1]}/0"
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()

    def _accept(self):
        """Register each connection worker before allowing it to handle traffic."""
        while not self.stop.is_set():
            try:
                downstream, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.stop.is_set():
                    return
                raise
            worker = threading.Thread(target=self._forward, args=(downstream,), daemon=True)
            with self.lock:
                self.sockets.append(downstream)
                self.workers.append(worker)
            worker.start()

    def _forward(self, downstream):
        """Relay complete responses except the first committed XADD reply."""
        upstream = None
        try:
            upstream = socket.create_connection(("127.0.0.1", self.redis_port), timeout=1)
            upstream.settimeout(None)
            with self.lock:
                self.sockets.append(upstream)
            with (
                downstream,
                upstream,
                downstream.makefile("rb") as requests,
                upstream.makefile("rb") as replies,
            ):
                while not self.stop.is_set():
                    request, values = _resp(requests)
                    upstream.sendall(request)
                    response, _ = _resp(replies)
                    if isinstance(values, list) and values[0].upper() == b"XADD":
                        with self.lock:
                            self.xadds += 1
                            drop = not self.dropped.is_set()
                            if drop:
                                self.dropped.set()
                        if drop:
                            downstream.shutdown(socket.SHUT_RDWR)
                            return
                    downstream.sendall(response)
        except (EOFError, ConnectionError):
            return
        except OSError as error:
            if not self.stop.is_set():
                self.errors.append(error)
        except Exception as error:
            self.errors.append(error)
        finally:
            downstream.close()
            if upstream is not None:
                upstream.close()

    def close(self):
        """Interrupt all blocking sockets and require every proxy thread to exit."""
        self.stop.set()
        self.listener.close()
        self.acceptor.join(timeout=2)
        with self.lock:
            sockets, workers = list(self.sockets), list(self.workers)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Completed connection handlers already closed these owned sockets.
                if connection.fileno() != -1:
                    raise
            connection.close()
        deadline = time.monotonic() + 2
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if self.acceptor.is_alive() or any(worker.is_alive() for worker in workers):
            raise AssertionError("reply proxy worker survived cleanup deadline")
        if self.errors:
            raise AssertionError(self.errors)
