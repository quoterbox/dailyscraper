"""Pure domain types without I/O."""

from dailyscraper.domain.artifacts import (
    PARTITION_RECORD_VERSION,
    BatchCommit,
    CommitRecord,
    PartitionRef,
    PartitionSeal,
    PartitionState,
)
from dailyscraper.domain.errors import ErrorCategory
from dailyscraper.domain.events import (
    EVENT_SCHEMA_VERSION,
    DeliveryRecord,
    DeliveryStatus,
    Event,
    EventLevel,
)
from dailyscraper.domain.limits import DIRECT_PROXY, Dimension, LeaseKey, LimitRule, lease_keys
from dailyscraper.domain.notifications import (
    Notification,
    NotificationRecord,
    NotificationStatus,
    Urgency,
)
from dailyscraper.domain.pages import PageOutcome, PageState, PageType
from dailyscraper.domain.proxies import HealthStatus, Proxy, ProxyHealthState
from dailyscraper.domain.runs import DEFAULT_PROJECT_ID, RunRecord, RunStatus, new_run_id
from dailyscraper.domain.tasks import (
    Attempt,
    AttemptStatus,
    Command,
    CommandKind,
    Task,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
)

__all__ = [
    "DEFAULT_PROJECT_ID",
    "DIRECT_PROXY",
    "EVENT_SCHEMA_VERSION",
    "PARTITION_RECORD_VERSION",
    "Attempt",
    "AttemptStatus",
    "BatchCommit",
    "Command",
    "CommandKind",
    "CommitRecord",
    "DeliveryRecord",
    "DeliveryStatus",
    "Dimension",
    "ErrorCategory",
    "Event",
    "EventLevel",
    "HealthStatus",
    "LeaseKey",
    "LimitRule",
    "Notification",
    "NotificationRecord",
    "NotificationStatus",
    "PageOutcome",
    "PageState",
    "PageType",
    "PartitionRef",
    "PartitionSeal",
    "PartitionState",
    "Proxy",
    "ProxyHealthState",
    "RunRecord",
    "RunStatus",
    "Task",
    "TaskKind",
    "TaskOutcome",
    "TaskSpec",
    "TaskStatus",
    "Urgency",
    "lease_keys",
    "new_run_id",
]
