"""Small stdlib-only JSON-RPC transport for bounded command-line probes."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO


class ProbeError(RuntimeError):
    """The child exited, timed out, or returned an invalid/error response."""


class StdioProbe:
    """Drain both pipes concurrently; only the queue read waits for a deadline.

    Each instance owns its child process and must be used as a context manager.
    Deadlines use ``time.monotonic()`` and cover the entire protocol exchange.
    """

    def __init__(self, command: list[str]) -> None:
        if os.name == "nt":
            command = [sys.executable, str(Path(__file__).with_name("_win_job_runner.py")), *command]
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        self._start_readers()

    def _start_readers(self) -> None:
        self._lines: queue.Queue[bytes | None] = queue.Queue(maxsize=256)
        self._stderr: deque[bytes] = deque(maxlen=16)
        self._stderr_lock = threading.Lock()
        self._closed = threading.Event()
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def __enter__(self) -> StdioProbe:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _put_line(self, line: bytes | None) -> None:
        while not self._closed.is_set():
            try:
                self._lines.put(line, timeout=0.1)
                return
            except queue.Full:
                continue

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._put_line(line)
                if self._closed.is_set():
                    break
        finally:
            self._put_line(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := self.proc.stderr.read1(4096):
            with self._stderr_lock:
                self._stderr.append(chunk)

    @property
    def stderr(self) -> str:
        with self._stderr_lock:
            return b"".join(self._stderr).decode("utf-8", "replace")[-2500:]

    def send(self, message: object) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProbeError(f"child closed stdin: {exc}") from exc

    def response(self, request_id: int, deadline: float) -> dict:
        while True:
            message = self.message(deadline)
            if message is None:
                reason = (
                    "stdout closed before response" if self.proc.poll() is not None else "response deadline exceeded"
                )
                raise self._failure(f"request {request_id}: {reason}")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("jsonrpc") != "2.0":
                raise ProbeError(f"request {request_id}: invalid JSON-RPC version")
            if "error" in message:
                raise ProbeError(f"request {request_id}: JSON-RPC error: {message['error']}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise ProbeError(f"request {request_id}: expected an object result")
            return result

    def message(self, deadline: float) -> dict | None:
        """Return the next valid JSON object, or ``None`` on EOF/timeout."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                raw = self._lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if raw is None:
                return None
            try:
                message = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(message, dict):
                return message

    def _failure(self, message: str) -> ProbeError:
        stderr = self.stderr.strip()
        return ProbeError(f"{message}; stderr: {stderr}" if stderr else message)

    def initialize(self, name: str, deadline: float) -> dict:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": name, "version": "0"},
                },
            }
        )
        result = self.response(1, deadline)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self, deadline: float) -> list[dict]:
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        result = self.response(2, deadline)
        tools = result.get("tools")
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or not isinstance(tool.get("name"), str) for tool in tools
        ):
            raise ProbeError("tools/list: expected a list of named tool objects")
        return tools

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            # The supervisor's non-inherited job handle owns all descendants,
            # including children whose original wrapper has already exited.
            if self.proc.poll() is None:
                self.proc.kill()
        else:
            # A session group remains valid after its leader exits. Always kill
            # it, not only when the direct child is still alive.
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.proc.wait(timeout=5)
        for thread in self._threads:
            thread.join(timeout=0.5)
        # A reader owns each pipe until it finishes; never block closing its lock.
        for pipe, thread in zip((self.proc.stdout, self.proc.stderr), self._threads, strict=True):
            if not thread.is_alive():
                self._close_pipe(pipe)

    @staticmethod
    def _close_pipe(pipe: BinaryIO | None) -> None:
        if pipe is not None:
            pipe.close()
