from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from multiprocessing.connection import Connection

from dailyscraper.browsers.messages import (
    Ack,
    Cancel,
    ItemsCommitted,
    Message,
    PartitionSealed,
    Progress,
    Shutdown,
    commit_ref,
    decode_to_slot,
    encode,
    seal_ref,
)
from dailyscraper.domain import BatchCommit, PartitionSeal


class TaskCancelled(Exception):
    pass


class CommitRejected(RuntimeError):
    pass


class SlotChannel:
    """The slot's end of the pipe: sends events, waits for acknowledgements, notices cancels."""

    def __init__(
        self,
        connection: Connection,
        *,
        ack_timeout: float = 60.0,
        progress_interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection = connection
        self._ack_timeout = ack_timeout
        self._progress_interval = progress_interval
        self._clock = clock
        self._inbox: deque[Message] = deque()
        self._send_lock = threading.Lock()
        self._last_progress = clock()
        self.attempt_id: str | None = None
        self.cancelled = False
        self.shutdown_requested = False

    def send(self, message: Message) -> None:
        with self._send_lock:
            self._connection.send_bytes(encode(message))

    def receive(self, timeout: float | None = None) -> Message | None:
        if self._inbox:
            return self._inbox.popleft()
        if not self._connection.poll(timeout):
            return None
        return decode_to_slot(self._connection.recv_bytes())

    def commit(self, batch: BatchCommit) -> None:
        self._request(ItemsCommitted(commit=batch), commit_ref(batch))

    def seal(self, seal: PartitionSeal) -> None:
        self._request(PartitionSealed(seal=seal), seal_ref(seal))

    def check_interrupted(self) -> None:
        self._drain()
        if self._clock() - self._last_progress >= self._progress_interval:
            self.send(Progress(attempt_id=self.attempt_id))
            self._last_progress = self._clock()
        if self.cancelled:
            raise TaskCancelled()

    def _request(self, message: Message, ref: str) -> None:
        self.send(message)
        deadline = self._clock() + self._ack_timeout
        while (remaining := deadline - self._clock()) > 0:
            if not self._connection.poll(remaining):
                break
            reply = decode_to_slot(self._connection.recv_bytes())
            if isinstance(reply, Ack) and reply.ref == ref:
                if not reply.ok:
                    raise CommitRejected(reply.error or f"{ref} rejected")
                self._last_progress = self._clock()
                return
            self._keep(reply)
        raise CommitRejected(f"no acknowledgement for {ref} in {self._ack_timeout}s")

    def _drain(self) -> None:
        while self._connection.poll(0):
            self._keep(decode_to_slot(self._connection.recv_bytes()))

    def _keep(self, message: Message) -> None:
        if isinstance(message, Cancel):
            self.cancelled = True
        elif isinstance(message, Shutdown):
            self.shutdown_requested = True
            self._inbox.append(message)
        else:
            self._inbox.append(message)
