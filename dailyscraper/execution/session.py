from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from dailyscraper.actions import ScraperActions
from dailyscraper.execution.context import PageContext
from dailyscraper.execution.loader import load_package_class
from dailyscraper.execution.navigator import Navigator
from dailyscraper.execution.sink import ItemSink
from dailyscraper.execution.snapshots import SnapshotWriter
from dailyscraper.execution.timing import Pacer
from dailyscraper.pipelines import DefaultPipeline
from dailyscraper.schema import ScraperPackage
from dailyscraper.settings import Settings
from dailyscraper.transport import PlaywrightBrowser, SessionPlan


@contextmanager
def open_pipeline(
    package: ScraperPackage,
    settings: Settings,
    sink: ItemSink,
    pacer: Pacer | None = None,
    snapshots: SnapshotWriter | None = None,
    plan: SessionPlan | None = None,
) -> Iterator[DefaultPipeline]:
    """One browser session running one scraper package in the current process."""

    pacer = pacer or Pacer()
    actions_class = load_package_class(package, "actions", "Actions", ScraperActions)
    pipeline_class = load_package_class(package, "pipeline", "Pipeline", DefaultPipeline)
    with PlaywrightBrowser(settings.browser, plan) as browser:
        actions = actions_class(
            browser.page, browser.finder, package.definition.options, pacer.check_interrupted
        )
        navigator = Navigator(
            page=browser.page,
            finder=browser.finder,
            definition=package.definition,
            actions=actions,
            browser=settings.browser,
            pacer=pacer,
        )
        yield pipeline_class(
            PageContext(
                definition=package.definition,
                page=browser.page,
                finder=browser.finder,
                navigator=navigator,
                actions=actions,
                sink=sink,
                pacer=pacer,
                snapshots=snapshots,
            )
        )
