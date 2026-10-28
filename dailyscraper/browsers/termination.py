from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TerminationResult:
    discovered: tuple[int, ...]
    killed: tuple[int, ...]
    remaining: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return not self.remaining


def terminate_tree(pid: int, *, grace_seconds: float = 5.0) -> TerminationResult:
    return terminate_trees([pid], grace_seconds=grace_seconds)


def terminate_trees(pids: Iterable[int], *, grace_seconds: float = 5.0) -> TerminationResult:
    """Terminate processes with all their descendants, killing what survives the grace period."""
    roots = {int(pid) for pid in pids if pid and int(pid) != os.getpid()}
    processes: dict[int, psutil.Process] = {}
    for root_pid in roots:
        try:
            root = psutil.Process(root_pid)
            processes[root.pid] = root
            for child in root.children(recursive=True):
                processes.setdefault(child.pid, child)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            logger.warning("Cannot inspect process %s: %s", root_pid, error)

    # Roots first, so they cannot start new browser children during cleanup.
    ordered = sorted(processes.values(), key=lambda process: process.pid not in roots)
    signalled = []
    for process in ordered:
        try:
            process.terminate()
            signalled.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            logger.warning("Cannot terminate process %s: %s", process.pid, error)
    _, alive = psutil.wait_procs(signalled, timeout=max(0.0, grace_seconds))

    killed = []
    for process in alive:
        try:
            process.kill()
            killed.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as error:
            logger.warning("Cannot kill process %s: %s", process.pid, error)
    _, still_alive = psutil.wait_procs(killed, timeout=2.0)
    result = TerminationResult(
        discovered=tuple(sorted(processes)),
        killed=tuple(sorted(process.pid for process in killed)),
        remaining=tuple(sorted(process.pid for process in still_alive if _running(process))),
    )
    if not result.complete:
        logger.warning("Processes survived termination: %s", result.remaining)
    return result


def _running(process: psutil.Process) -> bool:
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True
