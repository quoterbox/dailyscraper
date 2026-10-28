"""Where the proxy pool comes from: a static list, a residential gateway or a provider API.

Every source yields the same ``Proxy`` objects with stable ids, so allocation,
health and limits do not know the difference. A residential gateway is one
host whose sessions, carried in the login, pin separate exit IPs; a session's
``generation`` is part of its id in the login, so a new generation means a new
exit IP for the same ``proxy_id``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from dailyscraper.domain import Proxy
from dailyscraper.settings.models import ApiSource, GatewaySource, ProxiesSettings

logger = logging.getLogger(__name__)

# Providers put API keys into URL path segments; segments this long are hidden in errors.
SECRET_SEGMENT_LENGTH = 12

Fetch = Callable[[str, str, Mapping[str, str], float], Any]


class ProxySourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyPool:
    proxies: tuple[Proxy, ...] = ()
    gateway: GatewaySource | None = None

    @property
    def empty(self) -> bool:
        return not self.proxies

    def select(self, names: Collection[str] = ()) -> list[Proxy]:
        if not names:
            return list(self.proxies)
        by_id = {proxy.proxy_id: proxy for proxy in self.proxies}
        unknown = [name for name in names if name not in by_id]
        if unknown:
            raise ProxySourceError(f"unknown proxies {unknown}")
        return [by_id[name] for name in dict.fromkeys(names)]

    def resolve(self, proxy: Proxy, generation: int) -> Proxy:
        """The proxy with the credentials of a session generation."""
        if not proxy.rotating or self.gateway is None or generation == proxy.generation:
            return proxy
        index = next(
            number
            for number, candidate in enumerate(self.proxies, start=1)
            if candidate.proxy_id == proxy.proxy_id
        )
        return _gateway_proxy(self.gateway, index, generation)


def build_pool(
    settings: ProxiesSettings,
    *,
    environ: Mapping[str, str] = os.environ,
    fetch: Fetch | None = None,
) -> ProxyPool:
    if settings.source == "none":
        return ProxyPool()
    if settings.source == "static":
        return ProxyPool(_static(settings))
    if settings.source == "gateway":
        gateway = settings.gateway
        proxies = tuple(
            _gateway_proxy(gateway, index, 0) for index in range(1, gateway.sessions + 1)
        )
        logger.info("Gateway %s expanded into %s sessions", gateway.host, gateway.sessions)
        return ProxyPool(proxies, gateway)
    try:
        proxies = _api(settings.api, environ, fetch or _fetch_json)
    except ProxySourceError as error:
        if not settings.api.fallback_to_static:
            raise
        logger.warning("Proxy API unavailable, using the static list: %s", error)
        return ProxyPool(_static(settings))
    return ProxyPool(proxies)


def _static(settings: ProxiesSettings) -> tuple[Proxy, ...]:
    auth = settings.static.default_auth
    return tuple(
        Proxy(
            proxy_id=item.name,
            host=item.host,
            port=item.port,
            scheme=item.scheme,
            user=item.user or auth.user,
            password=item.password or auth.password,
            country_code=item.country_code,
            source="static",
        )
        for item in settings.static.items
    )


def _gateway_proxy(gateway: GatewaySource, index: int, generation: int) -> Proxy:
    values = {
        "index": index,
        "salt": gateway.session_salt,
        "user": gateway.user,
        "password": gateway.password or "",
        "generation": generation,
    }
    session = _render(gateway.session_id_template, values)
    values["session"] = session
    if gateway.port_range is not None:
        first, last = gateway.port_range
        port = first + (index - 1) % (last - first + 1)
    else:
        port = gateway.port
    return Proxy(
        proxy_id=_render(gateway.name_template, values),
        host=gateway.host,
        port=port,
        scheme=gateway.scheme,
        user=_render(gateway.session_template, values),
        password=_render(gateway.password_template, values)
        if gateway.password_template
        else gateway.password,
        country_code=gateway.country_code,
        source="gateway",
        rotating=True,
        generation=generation,
    )


def _api(api: ApiSource, environ: Mapping[str, str], fetch: Fetch) -> tuple[Proxy, ...]:
    url = (environ.get(api.url_env, "") if api.url_env else "") or api.url or ""
    if not url:
        raise ProxySourceError(f"proxies.api.url_env {api.url_env} is not set")
    headers = dict(api.headers)
    token = api.token or (environ.get(api.token_env, "") if api.token_env else "")
    if token:
        headers[api.token_header] = api.token_header_template.format(token=token)
    try:
        payload = fetch(url, api.method, headers, api.timeout_seconds)
    except Exception as error:
        message = f"{type(error).__name__}: {error}".replace(url, redact_url(url))
        raise ProxySourceError(f"proxy API request failed: {message}") from error
    _raise_for_business_errors(payload)
    items = _dotted(payload, api.items_field) if api.items_field else payload
    if not isinstance(items, list):
        raise ProxySourceError("proxy API response is not a list; set proxies.api.items_field")

    proxies = []
    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            continue
        host = _mapped(raw, api.field_map, "host") or api.host
        port = _mapped(raw, api.field_map, "port") or api.port
        if not host or not port:
            logger.warning("Proxy API item %s has no host or port, skipped", index)
            continue
        proxies.append(
            Proxy(
                proxy_id=str(_mapped(raw, api.field_map, "name") or f"api_{host}_{port}"),
                host=str(host),
                port=int(port),
                scheme=str(_mapped(raw, api.field_map, "scheme") or api.scheme),
                user=_mapped(raw, api.field_map, "user") or api.default_auth.user,
                password=_mapped(raw, api.field_map, "password") or api.default_auth.password,
                country_code=_mapped(raw, api.field_map, "country_code"),
                source="api",
            )
        )
    if not proxies:
        raise ProxySourceError("proxy API returned no proxies")
    ids = [proxy.proxy_id for proxy in proxies]
    if len(ids) != len(set(ids)):
        raise ProxySourceError("proxy API returned duplicate proxy names")
    logger.info("Proxy API returned %s proxies", len(proxies))
    return tuple(proxies)


def _fetch_json(url: str, method: str, headers: Mapping[str, str], timeout: float) -> Any:
    request = urllib.request.Request(url, method=method, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _raise_for_business_errors(payload: Any) -> None:
    # Some providers report a rejected key as HTTP 200 with errors[]; without this
    # a wrong key looks like an empty pool.
    if not isinstance(payload, dict):
        return
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        raise ProxySourceError(f"proxy API returned errors: {errors[:3]}")
    status = str(payload.get("status") or "").strip().lower()
    if status and status not in {"success", "ok"}:
        raise ProxySourceError(f"proxy API returned status {status!r}")


def redact_url(url: str) -> str:
    head, separator, _ = url.partition("?")
    scheme, marker, rest = head.partition("://")
    if not marker:
        scheme, rest = "", head
    host, _, path = rest.partition("/")
    segments = [
        "***" if len(segment) >= SECRET_SEGMENT_LENGTH and segment.isalnum() else segment
        for segment in path.split("/")
    ]
    rebuilt = f"{scheme}{marker}{host}" + (f"/{'/'.join(segments)}" if path else "")
    return f"{rebuilt}?***" if separator else rebuilt


def _render(template: str, values: Mapping[str, Any]) -> str:
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as error:
        raise ProxySourceError(f"invalid proxy template {template!r}: {error}") from error


def _mapped(raw: Mapping[str, Any], field_map: Mapping[str, str], name: str) -> Any:
    value = _dotted(raw, field_map.get(name, name))
    return None if value in ("", None) else value


def _dotted(payload: Any, path: str | None) -> Any:
    current = payload
    for part in (path or "").split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current
