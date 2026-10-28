from __future__ import annotations

import logging
import re
from pathlib import Path

from dailyscraper.transport import BrowserPage, is_browser_closed_error

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class SnapshotWriter:
    """Saves the HTML and a screenshot of failed pages, up to ``limit`` per writer."""

    def __init__(self, directory: Path, *, limit: int = 20, prefix: str = "") -> None:
        self.directory = Path(directory)
        self.limit = limit
        self.prefix = prefix
        self.saved: list[Path] = []

    def capture(self, page: BrowserPage, label: str) -> Path | None:
        if len(self.saved) >= self.limit:
            return None
        number = len(self.saved) + 1
        name = f"{self.prefix}{number:03d}-{_UNSAFE.sub('_', label)[:80]}"
        self.directory.mkdir(parents=True, exist_ok=True)
        html = self.directory / f"{name}.html"
        try:
            html.write_text(f"<!-- {page.url} -->\n{page.content()}", encoding="utf-8")
            page.screenshot(self.directory / f"{name}.png")
        except Exception as error:
            if is_browser_closed_error(error):
                raise
            logger.warning("Page snapshot %s failed: %s", name, error)
            if not html.exists():
                return None
        self.saved.append(html)
        return html
