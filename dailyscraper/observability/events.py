from __future__ import annotations

import logging
import uuid
from typing import Any

from dailyscraper.domain import Event, EventLevel
from dailyscraper.observability.redaction import redact
from dailyscraper.storage import Store

logger = logging.getLogger(__name__)


class RunEvents:
    def __init__(self, store: Store, *, run_id: str, project_id: str) -> None:
        self.store = store
        self.run_id = run_id
        self.project_id = project_id

    def emit(
        self,
        type_: str,
        *,
        level: EventLevel = EventLevel.INFO,
        scraper: str | None = None,
        **data: Any,
    ) -> Event:
        event = Event(
            event_id=uuid.uuid4().hex,
            run_id=self.run_id,
            project_id=self.project_id,
            type=type_,
            at=self.store.now(),
            level=level,
            scraper=scraper,
            data=redact(data),
        )
        try:
            self.store.events.append([event])
        except Exception:
            logger.warning("Could not store event %s", type_, exc_info=True)
        return event
