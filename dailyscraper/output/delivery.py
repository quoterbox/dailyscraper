from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dailyscraper.domain import DeliveryRecord, DeliveryStatus
from dailyscraper.output.archive import make_archive
from dailyscraper.settings.models import DeliverySettings

logger = logging.getLogger(__name__)

RETRY_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class DeliveryContext:
    run_id: str
    project_id: str
    date: str
    succeeded: bool
    work_dir: Path


class Destination(Protocol):
    kind: str

    def deliver(self, files: Sequence[Path], context: DeliveryContext) -> str:
        """Send the files and return where they went."""
        ...


class FolderDestination:
    kind = "folder"

    def __init__(self, path_template: str, base_dir: Path) -> None:
        self.path_template = path_template
        self.base_dir = Path(base_dir)

    def deliver(self, files: Sequence[Path], context: DeliveryContext) -> str:
        target = Path(
            self.path_template.format(
                run_id=context.run_id, project=context.project_id, date=context.date
            )
        )
        if not target.is_absolute():
            target = self.base_dir / target
        target.mkdir(parents=True, exist_ok=True)
        for path in files:
            temporary = target / f".{path.name}.tmp"
            shutil.copyfile(path, temporary)
            os.replace(temporary, target / path.name)
        return str(target.resolve())


def destination_for(settings: DeliverySettings, base_dir: Path) -> Destination:
    if settings.type == "folder":
        return FolderDestination(settings.path, base_dir)
    raise ValueError(f"unknown delivery type {settings.type!r}")


@dataclass
class Deliverer:
    """Delivers output files per settings; a failed delivery is recorded, never raised."""

    base_dir: Path
    clock: Callable[[], str]
    record: Callable[[DeliveryRecord], None]
    sleep: Callable[[float], None] = time.sleep
    destinations: Callable[[DeliverySettings, Path], Destination] = destination_for

    def run(
        self,
        deliveries: Sequence[DeliverySettings],
        outputs: Mapping[str, Sequence[Path]],
        context: DeliveryContext,
    ) -> list[DeliveryRecord]:
        results = []
        for settings in deliveries:
            if settings.when == "success" and not context.succeeded:
                result = self._result(
                    settings, context, DeliveryStatus.SKIPPED, 0, error="run did not succeed"
                )
            else:
                formats = settings.formats or tuple(outputs)
                files = [path for fmt in formats for path in outputs.get(fmt, ())]
                result = self._deliver(settings, files, context)
            self.record(result)
            results.append(result)
        return results

    def _deliver(
        self, settings: DeliverySettings, files: list[Path], context: DeliveryContext
    ) -> DeliveryRecord:
        error = None
        for attempt in range(1, settings.attempts + 1):
            try:
                to_send = files
                if settings.archive and files:
                    archive = context.work_dir / f"{context.run_id}-{settings.name}.zip"
                    to_send = [make_archive(files, archive)]
                location = self.destinations(settings, self.base_dir).deliver(to_send, context)
                logger.info("Delivered %s to %s", settings.name, location)
                return self._result(
                    settings,
                    context,
                    DeliveryStatus.DELIVERED,
                    attempt,
                    location=location,
                    files=tuple(path.name for path in to_send),
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Delivery %s attempt %s/%s failed: %s",
                    settings.name,
                    attempt,
                    settings.attempts,
                    error,
                )
                if attempt < settings.attempts:
                    self.sleep(RETRY_DELAY_SECONDS * 2 ** (attempt - 1))
        return self._result(
            settings, context, DeliveryStatus.FAILED, settings.attempts, error=error
        )

    def _result(
        self,
        settings: DeliverySettings,
        context: DeliveryContext,
        status: DeliveryStatus,
        attempts: int,
        *,
        location: str | None = None,
        files: tuple[str, ...] = (),
        error: str | None = None,
    ) -> DeliveryRecord:
        return DeliveryRecord(
            delivery_id=f"{context.run_id}:{settings.name}",
            run_id=context.run_id,
            name=settings.name,
            kind=settings.type,
            status=status,
            attempts=attempts,
            at=self.clock(),
            location=location,
            files=files,
            error=error,
        )
