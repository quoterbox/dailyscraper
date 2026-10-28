from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dailyscraper.domain import HealthStatus, Proxy, ProxyHealthState
from dailyscraper.proxies import (
    ProxyAllocator,
    ProxyHealth,
    ProxyPool,
    ProxySourceError,
    build_pool,
    check_proxies,
    effective_status,
    record,
    redact_url,
)
from dailyscraper.settings.models import ProxiesSettings, ProxyCheckSettings, ProxyHealthSettings
from dailyscraper.storage.sqlite import SQLiteStore
from tests.harness.connect_proxy import ProgrammableConnectProxy

POLICY = ProxyHealthSettings(cooldown_seconds=60, max_cooldown_seconds=200, backoff_factor=2)
NOW = "2026-10-28T10:00:00.000+00:00"


def static_settings(*names: str, **extra) -> ProxiesSettings:
    return ProxiesSettings.model_validate(
        {
            "source": "static",
            "static": {
                "default_auth": {"user": "u", "password": "secret"},
                "items": [
                    {"name": name, "host": "10.0.0.1", "port": 8000 + number, **extra}
                    for number, name in enumerate(names, start=1)
                ],
            },
        }
    )


def gateway_settings(**extra) -> ProxiesSettings:
    return ProxiesSettings.model_validate(
        {
            "source": "gateway",
            "gateway": {
                "host": "res.example",
                "port": 10000,
                "sessions": 3,
                "user": "acc",
                "password": "pw",
                "session_template": "{user}_c_RU_s_{session}_ttl_30m",
                **extra,
            },
        }
    )


def test_static_pool_uses_default_auth_and_selects_by_name() -> None:
    pool = build_pool(static_settings("p1", "p2"))

    assert [(p.proxy_id, p.user, p.password, p.port) for p in pool.proxies] == [
        ("p1", "u", "secret", 8001),
        ("p2", "u", "secret", 8002),
    ]
    assert [p.proxy_id for p in pool.select(["p2"])] == ["p2"]
    with pytest.raises(ProxySourceError, match="unknown"):
        pool.select(["p9"])
    assert build_pool(ProxiesSettings()).empty


def test_gateway_sessions_have_stable_ids_and_generations() -> None:
    pool = build_pool(gateway_settings(port_range=[10000, 10001]))

    first = pool.proxies[0]
    assert [p.proxy_id for p in pool.proxies] == ["res_01", "res_02", "res_03"]
    assert first.user == "acc_c_RU_s_ds01g0_ttl_30m" and first.rotating
    assert [p.port for p in pool.proxies] == [10000, 10001, 10000]
    rotated = pool.resolve(first, 2)
    assert (rotated.proxy_id, rotated.user, rotated.generation) == (
        "res_01",
        "acc_c_RU_s_ds01g2_ttl_30m",
        2,
    )
    assert rotated.identity() != first.identity()


def test_gateway_must_pin_the_session() -> None:
    with pytest.raises(ValidationError, match="exit IP"):
        gateway_settings(session_template="{user}")


def api_settings(**extra) -> ProxiesSettings:
    return ProxiesSettings.model_validate(
        {
            "source": "api",
            "static": {"items": [{"name": "backup", "host": "10.0.0.9", "port": 9000}]},
            "api": {
                "url": "https://provider.example/v1/abcdefghijklmnop/list",
                "items_field": "data.items",
                "field_map": {"name": "title", "country_code": "geo.cc"},
                "host": "gw.example",
                "port": 7000,
                **extra,
            },
        }
    )


def test_api_pool_maps_items_and_falls_back() -> None:
    payload = {
        "status": "success",
        "data": {"items": [{"title": "a1", "geo": {"cc": "RU"}}, {"title": "a2", "port": 7001}]},
    }

    pool = build_pool(api_settings(), fetch=lambda *args: payload)

    assert [(p.proxy_id, p.host, p.port, p.country_code) for p in pool.proxies] == [
        ("a1", "gw.example", 7000, "RU"),
        ("a2", "gw.example", 7001, None),
    ]
    with pytest.raises(ProxySourceError, match="errors"):
        build_pool(api_settings(), fetch=lambda *args: {"errors": ["bad key"]})

    def broken(url, *args):
        raise OSError(f"cannot reach {url}")

    fallback = build_pool(api_settings(fallback_to_static=True), fetch=broken)
    assert [p.proxy_id for p in fallback.proxies] == ["backup"]
    with pytest.raises(ProxySourceError) as error:
        build_pool(api_settings(), fetch=broken)
    assert "abcdefghijklmnop" not in str(error.value)


def test_url_secrets_are_hidden() -> None:
    assert redact_url("https://p.example/v1/abcdefghijklmnop/list?key=1") == (
        "https://p.example/v1/***/list?***"
    )


def state(**fields) -> ProxyHealthState:
    return ProxyHealthState(site="a.ru", proxy_id="p1", **fields)


def test_tracked_failures_degrade_then_open_and_success_heals() -> None:
    once = record(state(), "timeout", now=NOW, rotating=False, policy=POLICY)
    twice = record(once, "network", now=NOW, rotating=False, policy=POLICY)

    assert once.status is HealthStatus.DEGRADED
    assert twice.status is HealthStatus.OPEN and twice.cooldown_until > NOW
    healed = record(once, None, now=NOW, rotating=False, policy=POLICY)
    assert (healed.status, healed.consecutive_failures) == (HealthStatus.HEALTHY, 0)
    neutral = record(once, "not_found", now=NOW, rotating=False, policy=POLICY)
    assert neutral.status is HealthStatus.DEGRADED and neutral.failures == 1


def test_block_opens_at_once_with_growing_cooldown() -> None:
    first = record(state(), "blocked", now=NOW, rotating=False, policy=POLICY)
    assert first.cooldown_until == "2026-10-28T10:01:00.000+00:00"

    later = "2026-10-28T10:02:00.000+00:00"
    assert effective_status(first, later) is HealthStatus.HALF_OPEN
    second = record(first, "timeout", now=later, rotating=False, policy=POLICY)
    assert second.status is HealthStatus.OPEN
    assert second.cooldown_until == "2026-10-28T10:04:00.000+00:00"
    third = record(second, "captcha", now=later, rotating=False, policy=POLICY)
    assert third.cooldown_until == "2026-10-28T10:05:20.000+00:00"


def test_rotating_proxy_gets_a_new_session_instead_of_a_cooldown() -> None:
    rotated = record(state(), "captcha", now=NOW, rotating=True, policy=POLICY)

    assert (rotated.status, rotated.generation, rotated.open_count) == (
        HealthStatus.UNKNOWN,
        1,
        1,
    )
    assert rotated.cooldown_until is None


def test_allocator_skips_open_ranks_and_avoids(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    health = ProxyHealth(store, POLICY)
    pool = build_pool(static_settings("p1", "p2", "p3", "p4"))
    allocator = ProxyAllocator(pool, health)
    proxy = {p.proxy_id: p for p in pool.proxies}
    health.report("a.ru", proxy["p1"], "blocked")
    health.report("a.ru", proxy["p2"], "timeout")
    health.report("a.ru", proxy["p4"], None)

    assert [p.proxy_id for p in allocator.candidates("a.ru")] == ["p4", "p3", "p2"]
    assert [p.proxy_id for p in allocator.candidates("b.ru")] == ["p1", "p2", "p3", "p4"]
    assert [p.proxy_id for p in allocator.candidates("a.ru", avoid=["p4"])] == ["p3", "p2"]
    assert [p.proxy_id for p in allocator.candidates("a.ru", ["p4"], avoid=["p4"])] == ["p4"]
    assert ProxyAllocator(ProxyPool(), health).candidates("a.ru") == [None]


def test_allocator_hands_out_the_current_gateway_session(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "s.sqlite")
    health = ProxyHealth(store, POLICY)
    pool = build_pool(gateway_settings())
    health.report("a.ru", pool.proxies[0], "blocked")

    first = ProxyAllocator(pool, health).candidates("a.ru")[0]

    assert first.proxy_id == "res_01" and first.generation == 1
    assert "s_ds01g1" in first.user


def test_network_check_through_real_proxies(ip_server) -> None:
    settings = ProxyCheckSettings(url=ip_server, timeout_seconds=5)
    with (
        ProgrammableConnectProxy("tunnel", credentials=("u", "pw")) as good,
        ProgrammableConnectProxy("proxy_auth") as bad,
    ):
        proxies = [
            Proxy("good", good.endpoint.host, good.endpoint.port, user="u", password="pw"),
            Proxy("bad", bad.endpoint.host, bad.endpoint.port, user="u", password="pw"),
            Proxy("dead", "127.0.0.1", 9, user="u", password="pw"),
        ]

        results = {result.proxy_id: result for result in check_proxies(proxies, settings)}

    assert results["good"].ok and results["good"].exit_ip == "203.0.113.7"
    assert good.log.requests == [ip_server]
    assert (results["bad"].ok, results["bad"].category) == (False, "proxy_auth")
    assert not results["dead"].ok and results["dead"].category == "network"


def test_gateway_must_rotate_through_the_generation() -> None:
    with pytest.raises(ValidationError, match="generation"):
        gateway_settings(session_id_template="{salt}{index:02d}")
