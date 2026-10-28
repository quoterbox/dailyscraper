"""Messages between the dispatcher and slot processes.

Every message is a versioned model serialized to JSON, so the same protocol can
later run between machines.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from dailyscraper.domain import BatchCommit, PartitionSeal, TaskKind, TaskOutcome

PROTOCOL_VERSION = 1


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    v: int = PROTOCOL_VERSION


class RunTask(Message):
    type: Literal["run_task"] = "run_task"
    task_id: str
    attempt_id: str
    kind: TaskKind
    payload: dict[str, Any]


class Ack(Message):
    type: Literal["ack"] = "ack"
    ref: str
    ok: bool
    error: str | None = None


class Cancel(Message):
    """Abort the current task at the next interruption check."""

    type: Literal["cancel"] = "cancel"


class Shutdown(Message):
    """Finish the current task, close the session and exit."""

    type: Literal["shutdown"] = "shutdown"


class SlotReady(Message):
    type: Literal["ready"] = "ready"
    pid: int


class SlotFailed(Message):
    type: Literal["slot_failed"] = "slot_failed"
    error: str


class TaskStarted(Message):
    type: Literal["task_started"] = "task_started"
    attempt_id: str


class Progress(Message):
    type: Literal["progress"] = "progress"
    attempt_id: str | None = None


class ItemsCommitted(Message):
    type: Literal["items_committed"] = "items_committed"
    commit: BatchCommit


class PartitionSealed(Message):
    type: Literal["partition_sealed"] = "partition_sealed"
    seal: PartitionSeal


class TaskFinished(Message):
    type: Literal["task_finished"] = "task_finished"
    attempt_id: str
    outcome: TaskOutcome


class SessionBroken(Message):
    """The browser session is unusable; the slot exits after this message."""

    type: Literal["session_broken"] = "session_broken"
    reason: str


ToSlot = Annotated[RunTask | Ack | Cancel | Shutdown, Field(discriminator="type")]
FromSlot = Annotated[
    SlotReady
    | SlotFailed
    | TaskStarted
    | Progress
    | ItemsCommitted
    | PartitionSealed
    | TaskFinished
    | SessionBroken,
    Field(discriminator="type"),
]

_TO_SLOT: TypeAdapter[Any] = TypeAdapter(ToSlot)
_FROM_SLOT: TypeAdapter[Any] = TypeAdapter(FromSlot)


class ProtocolError(ValueError):
    pass


def encode(message: Message) -> bytes:
    return message.model_dump_json().encode("utf-8")


def decode_to_slot(data: bytes) -> RunTask | Ack | Cancel | Shutdown:
    return _decode(_TO_SLOT, data)


def decode_from_slot(data: bytes) -> Message:
    return _decode(_FROM_SLOT, data)


def _decode(adapter: TypeAdapter[Any], data: bytes) -> Any:
    message = adapter.validate_json(data)
    if message.v != PROTOCOL_VERSION:
        raise ProtocolError(f"protocol version {message.v}, expected {PROTOCOL_VERSION}")
    return message


def commit_ref(commit: BatchCommit) -> str:
    return f"commit:{commit.commit_id}"


def seal_ref(seal: PartitionSeal) -> str:
    return f"seal:{seal.partition.path}"
