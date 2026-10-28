"""Circuit breaker per pair (site, proxy), kept in the store so every run sees it.

Blocks, captchas and proxy auth failures open the circuit at once; timeouts and
network errors after a run of failures. An open static proxy rests for a
growing cooldown, then one session may probe it again. A rotating gateway
session does not wait: opening its circuit moves it to a new generation, that
is a new exit IP under the same proxy id.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from dailyscraper.domain import HealthStatus, Proxy, ProxyHealthState
from dailyscraper.settings.models import ProxyHealthSettings
from dailyscraper.storage import Store

RANK = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.DEGRADED: 2,
    HealthStatus.HALF_OPEN: 3,
}


@dataclass(frozen=True)
class HealthTransition:
    before: ProxyHealthState
    after: ProxyHealthState

    @property
    def opened(self) -> bool:
        return self.after.open_count > self.before.open_count

    @property
    def rotated(self) -> bool:
        return self.after.generation != self.before.generation


def effective_status(state: ProxyHealthState, now: str) -> HealthStatus:
    if state.status is HealthStatus.OPEN and (
        state.cooldown_until is None or state.cooldown_until <= now
    ):
        return HealthStatus.HALF_OPEN
    return state.status


def record(
    state: ProxyHealthState,
    category: str | None,
    *,
    now: str,
    rotating: bool,
    policy: ProxyHealthSettings,
) -> ProxyHealthState:
    """State after one session result; ``category`` is ``None`` for a success."""
    if category is None:
        return replace(
            state,
            status=HealthStatus.HEALTHY,
            consecutive_failures=0,
            successes=state.successes + 1,
            cooldown_until=None,
            last_category=None,
            updated_at=now,
        )
    immediate = category in policy.immediate_open_categories
    if not immediate and category not in policy.tracked_categories:
        return replace(state, updated_at=now)
    failed = replace(
        state,
        failures=state.failures + 1,
        consecutive_failures=state.consecutive_failures + 1,
        last_category=category,
        updated_at=now,
    )
    opens = (
        immediate
        or failed.consecutive_failures >= policy.consecutive_failures_to_open
        or effective_status(state, now) is HealthStatus.HALF_OPEN
    )
    if not opens:
        return replace(failed, status=HealthStatus.DEGRADED)
    if rotating:
        return replace(
            failed,
            status=HealthStatus.UNKNOWN,
            generation=state.generation + 1,
            consecutive_failures=0,
            open_count=state.open_count + 1,
            cooldown_until=None,
        )
    open_count = state.open_count + 1
    cooldown = min(
        policy.max_cooldown_seconds,
        policy.cooldown_seconds * policy.backoff_factor ** (open_count - 1),
    )
    until = datetime.fromisoformat(now) + timedelta(seconds=cooldown)
    return replace(
        failed,
        status=HealthStatus.OPEN,
        open_count=open_count,
        cooldown_until=until.isoformat(timespec="milliseconds"),
    )


class ProxyHealth:
    def __init__(self, store: Store, policy: ProxyHealthSettings) -> None:
        self.store = store
        self.policy = policy

    def states(self, site: str, proxy_ids: Sequence[str]) -> dict[str, ProxyHealthState]:
        return self.store.proxy_health.states(site, proxy_ids)

    def report(self, site: str, proxy: Proxy, category: str | None) -> HealthTransition:
        def change(state: ProxyHealthState, now: str) -> ProxyHealthState:
            return record(state, category, now=now, rotating=proxy.rotating, policy=self.policy)

        before, after = self.store.proxy_health.update(site, proxy.proxy_id, change)
        return HealthTransition(before, after)

    def open_now(self, site: str, proxy: Proxy, category: str) -> HealthTransition:
        """Open the circuit regardless of the failure count, after a failed network check."""
        policy = self.policy.model_copy(
            update={
                "immediate_open_categories": (
                    *self.policy.immediate_open_categories,
                    category,
                )
            }
        )

        def change(state: ProxyHealthState, now: str) -> ProxyHealthState:
            return record(state, category, now=now, rotating=False, policy=policy)

        before, after = self.store.proxy_health.update(site, proxy.proxy_id, change)
        return HealthTransition(before, after)


HealthChange = Callable[[ProxyHealthState, str], ProxyHealthState]
