"""A plain HTTP request through each proxy before browsers start, to catch dead proxies cheaply."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dailyscraper.detection import classify_error
from dailyscraper.domain import Proxy
from dailyscraper.settings.models import ProxyCheckSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    proxy_id: str
    ok: bool
    seconds: float
    exit_ip: str | None = None
    category: str | None = None
    error: str | None = None


Opener = Callable[[Proxy, ProxyCheckSettings], bytes]


def check_proxies(
    proxies: Sequence[Proxy], settings: ProxyCheckSettings, *, opener: Opener | None = None
) -> list[CheckResult]:
    request = opener or _request
    with ThreadPoolExecutor(max_workers=min(settings.parallel, max(1, len(proxies)))) as pool:
        return list(pool.map(lambda proxy: _check(proxy, settings, request), proxies))


def _check(proxy: Proxy, settings: ProxyCheckSettings, request: Opener) -> CheckResult:
    started = time.monotonic()
    try:
        body = request(proxy, settings)
        exit_ip = None
        if settings.ip_field:
            exit_ip = str(json.loads(body.decode("utf-8")).get(settings.ip_field) or "") or None
        result = CheckResult(proxy.proxy_id, True, time.monotonic() - started, exit_ip=exit_ip)
    except Exception as error:
        result = CheckResult(
            proxy.proxy_id,
            False,
            time.monotonic() - started,
            category=classify_error(error).value,
            error=f"{type(error).__name__}: {error}",
        )
        logger.warning("Proxy %s failed the network check: %s", proxy.proxy_id, result.error)
    return result


def _request(proxy: Proxy, settings: ProxyCheckSettings) -> bytes:
    handler = urllib.request.ProxyHandler({"http": proxy.url(), "https": proxy.url()})
    opener = urllib.request.build_opener(handler)
    with opener.open(settings.url, timeout=settings.timeout_seconds) as response:
        return response.read()
