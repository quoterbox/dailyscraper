from __future__ import annotations

from dataclasses import dataclass

from dailyscraper.actions import ScraperActions
from dailyscraper.execution.navigator import Navigator
from dailyscraper.execution.sink import ItemSink
from dailyscraper.execution.timing import Pacer
from dailyscraper.schema import ScraperDefinition
from dailyscraper.transport import BrowserPage, ElementFinder


@dataclass(frozen=True)
class PageContext:
    definition: ScraperDefinition
    page: BrowserPage
    finder: ElementFinder
    navigator: Navigator
    actions: ScraperActions
    sink: ItemSink
    pacer: Pacer
