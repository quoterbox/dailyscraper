from __future__ import annotations

from collections.abc import Collection

from dailyscraper.domain import HealthStatus, Proxy
from dailyscraper.proxies.health import RANK, ProxyHealth, effective_status
from dailyscraper.proxies.sources import ProxyPool


class ProxyAllocator:
    """The one place that orders proxies for a new session of a site.

    Open circuits are skipped; the rest go healthy first, then unknown,
    degraded and half-open, keeping the pool order within a rank. Proxies a
    task already failed on are avoided while others remain. ``None`` stands
    for the direct connection when no proxies are configured.
    """

    def __init__(self, pool: ProxyPool, health: ProxyHealth) -> None:
        self.pool = pool
        self.health = health

    def candidates(
        self, site: str, names: Collection[str] = (), avoid: Collection[str] = ()
    ) -> list[Proxy | None]:
        if self.pool.empty:
            return [None]
        proxies = self.pool.select(names)
        states = self.health.states(site, [proxy.proxy_id for proxy in proxies])
        now = self.health.store.now()
        usable: list[tuple[int, int, Proxy]] = []
        for position, proxy in enumerate(proxies):
            state = states[proxy.proxy_id]
            status = effective_status(state, now)
            if status is HealthStatus.OPEN:
                continue
            resolved = self.pool.resolve(proxy, state.generation)
            usable.append((RANK[status], position, resolved))
        preferred = [item for item in usable if item[2].proxy_id not in avoid]
        ranked = sorted(preferred or usable, key=lambda item: (item[0], item[1]))
        return [proxy for _, _, proxy in ranked]
