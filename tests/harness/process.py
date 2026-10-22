from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import psutil


@dataclass(frozen=True)
class CliResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class ManagedProcess:
    command: tuple[str, ...]
    process: subprocess.Popen
    stdout_path: Path
    stderr_path: Path
    _stdout_handle: BinaryIO = field(repr=False)
    _stderr_handle: BinaryIO = field(repr=False)

    @classmethod
    def start(
        cls,
        command: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        stdout_path: Path,
        stderr_path: Path,
    ) -> ManagedProcess:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_handle = stdout_path.open("wb")
        stderr_handle = stderr_path.open("wb")

        popen_kwargs: dict = {
            "cwd": str(cwd),
            "env": dict(environment),
            "stdout": stdout_handle,
            "stderr": stderr_handle,
            "stdin": subprocess.DEVNULL,
            "shell": False,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(list(command), **popen_kwargs)
        except Exception:
            stdout_handle.close()
            stderr_handle.close()
            raise

        return cls(
            command=tuple(str(part) for part in command),
            process=process,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            _stdout_handle=stdout_handle,
            _stderr_handle=stderr_handle,
        )

    @property
    def pid(self) -> int:
        return int(self.process.pid)

    @property
    def is_running(self) -> bool:
        return self.process.poll() is None

    def wait(self, timeout: float | None = None) -> int:
        try:
            return int(self.process.wait(timeout=timeout))
        finally:
            if self.process.poll() is not None:
                self.close_output()

    def send_interrupt(self) -> None:
        if not self.is_running:
            return
        if os.name == "nt":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(self.pid), signal.SIGINT)

    def terminate_tree(self, *, grace_seconds: float = 5.0) -> None:
        if not self.is_running:
            self.close_output()
            return

        try:
            parent = psutil.Process(self.pid)
        except psutil.Error:
            self.close_output()
            return

        descendants = parent.children(recursive=True)
        processes = [*descendants, parent]
        for process in reversed(processes):
            try:
                process.terminate()
            except psutil.Error:
                continue

        _, alive = psutil.wait_procs(processes, timeout=grace_seconds)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                continue
        psutil.wait_procs(alive, timeout=max(1.0, grace_seconds))
        self.close_output()

    def crash_tree(self, *, timeout: float = 5.0) -> tuple[int, ...]:
        """Abruptly kill this test-owned tree without graceful signals."""

        if not self.is_running:
            self.close_output()
            return ()

        try:
            parent = psutil.Process(self.pid)
        except psutil.NoSuchProcess:
            self.close_output()
            return ()

        descendants = parent.children(recursive=True)
        processes = [parent, *descendants]
        process_ids = tuple(process.pid for process in processes)
        for process in processes:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                continue

        _, alive = psutil.wait_procs(processes, timeout=max(1.0, timeout))
        if alive:
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    continue
            _, alive = psutil.wait_procs(alive, timeout=max(1.0, timeout))

        try:
            self.process.wait(timeout=max(1.0, timeout))
        finally:
            self.close_output()

        if alive:
            raise TimeoutError(
                "Test process tree survived forced termination: "
                f"{[process.pid for process in alive]}"
            )
        return process_ids

    def read_stdout(self) -> str:
        self._flush_output()
        return _read_text(self.stdout_path)

    def read_stderr(self) -> str:
        self._flush_output()
        return _read_text(self.stderr_path)

    def close_output(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if not handle.closed:
                handle.flush()
                handle.close()

    def _flush_output(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if not handle.closed:
                handle.flush()


def run_cli(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float = 30.0,
) -> CliResult:
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=dict(environment),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
    return CliResult(
        command=tuple(str(part) for part in command),
        returncode=int(completed.returncode),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def wait_for_process_exit(
    process: ManagedProcess,
    *,
    timeout: float,
    interval: float = 0.1,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        returncode = process.process.poll()
        if returncode is not None:
            process.close_output()
            return int(returncode)
        time.sleep(interval)
    raise TimeoutError(f"Process did not exit within {timeout:.1f}s: {process.command}")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")
