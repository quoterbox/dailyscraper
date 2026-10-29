"""Convert a previous-format scraper package config to scraper.json schema_version 3.

    uv run python tools/convert_scraper.py SOURCE_DIR TARGET_DIR

SOURCE_DIR holds the previous ``scrapper.json``; TARGET_DIR receives
``scraper.json``. Python hooks are not converted: their names changed and they
must be ported by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dailyscraper.schema import ScraperPackageError, load_scraper_package

SOURCE_FILE = "scrapper.json"
TARGET_FILE = "scraper.json"

PAGE_STATE_SELECTOR_KEYS = {
    "ready_selectors",
    "loading_selectors",
    "empty_selectors",
    "empty_after_wait_selectors",
    "not_found_selectors",
}
DROPPED_RELOAD_STATES = {"has_items"}
DISCOVERY_KEYS = {
    "enabled",
    "probe_wait_seconds",
    "settle_seconds",
    "empty_confirmations",
    "unknown_retries",
    "on_unknown",
    "on_blocked",
    "empty_when_no_items_text_markers",
}
INPUT_KEYS = {
    "encoding",
    "delimiter",
    "detail_link_field",
    "required_fields",
    "identity_fields",
    "deduplicate_by_fields",
}
LIST_WAITS = {
    "wait_item_body": "item_body",
    "wait_list_item": "item",
    "wait_list_item_field": "field",
    "wait_next_page_button": "next_page",
    "wait_last_page": "last_page",
}
LIST_DELAYS = {
    "delay_before_open_next_page": "before_next_page",
    "delay_between_list_item": "between_items",
}
DETAIL_WAITS = {
    "wait_detail_page": "page",
    "wait_detail_field": "field",
    "wait_detail_property": "property",
}
DETAIL_DELAYS = {"delay_between_detail_item": "between_items"}
AUTH_WAITS = {"wait_login_form": "login_form"}
AUTH_DELAYS = {
    "delay_after_login_entry": "after_login_entry",
    "delay_after_password_entry": "after_password_entry",
}
OBSOLETE_TIME_OPTIONS = {
    "delay_before_close",
    "delay_before_open_modal",
    "delay_between_review",
    "delay_for_count_page_items",
}
SCROLL_OFFSETS = {
    "scroll_origin_x_offset": "origin_x",
    "scroll_origin_y_offset": "origin_y",
    "scroll_delta_x": "delta_x",
    "scroll_delta_y": "delta_y",
}


class Conversion:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def unknown(self, where: str, data: dict[str, Any], known: set[str]) -> None:
        for key in sorted(set(data) - known):
            self.warn(f"{where}.{key}: not converted")

    def selector(
        self, data: dict[str, Any], where: str, extra_keys: frozenset[str] = frozenset()
    ) -> dict[str, Any] | None:
        self.unknown(
            where,
            data,
            {
                "CSS",
                "XPATH",
                "RELATIVE_XPATH",
                "MAIN_XPATH",
                "ATTRIBUTE",
                "WAIT",
                "PARENT_TEXT",
                "VALUE_PREPARE",
                "FALLBACK_SELECTORS",
            }
            | extra_keys,
        )
        locators = _locators(data)
        for index, fallback in enumerate(data.get("FALLBACK_SELECTORS") or []):
            fallback_where = f"{where}.FALLBACK_SELECTORS[{index}]"
            self.unknown(fallback_where, fallback, {"CSS", "XPATH", "WAIT", "ATTRIBUTE"})
            if fallback.get("ATTRIBUTE", data.get("ATTRIBUTE")) != data.get("ATTRIBUTE"):
                self.warn(
                    f"{fallback_where}: a different ATTRIBUTE is not supported for alternatives"
                )
            locators.extend(_locators(fallback))
        if not locators:
            return None
        result: dict[str, Any] = (
            dict(locators[0]) if len(locators) == 1 else {"selectors": locators}
        )
        if data.get("ATTRIBUTE"):
            result["attribute"] = data["ATTRIBUTE"]
        if data.get("WAIT") is not None:
            result["wait"] = data["WAIT"]
        if data.get("PARENT_TEXT"):
            result["parent_text"] = data["PARENT_TEXT"]
        if data.get("VALUE_PREPARE"):
            result["prepare"] = self.prepare(data["VALUE_PREPARE"], f"{where}.VALUE_PREPARE")
        return result

    def prepare(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        self.unknown(where, data, {"REGULAR_EXPRESSION", "REGEXP", "TYPE", "REPLACE_WITH"})
        result: dict[str, Any] = {"regex": data.get("REGULAR_EXPRESSION") or data.get("REGEXP")}
        mode = str(data.get("TYPE") or "FIND").lower()
        if mode != "find":
            result["mode"] = mode
        if data.get("REPLACE_WITH"):
            result["replace_with"] = data["REPLACE_WITH"]
        return result

    def fields(
        self, data: dict[str, Any], where: str, required: list[str] | None = None
    ) -> dict[str, Any]:
        result = {}
        extra_keys = frozenset({"REQUIRED"}) if required is not None else frozenset()
        for name, selector in data.items():
            converted = self.selector(selector, f"{where}.{name}", extra_keys)
            if converted is not None:
                result[name] = converted
                if required is not None and selector.get("REQUIRED"):
                    required.append(name)
        return result

    def item_sample(
        self, data: dict[str, Any], where: str, field_names: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], str | None]:
        self.unknown(where, data, {"item_body", "list_fields"})
        sample: dict[str, Any] = {}
        main_xpath = None
        body = data.get("item_body") or {}
        if body:
            main_xpath = body.get("MAIN_XPATH")
            converted = self.selector(body, f"{where}.item_body")
            if converted is not None:
                sample["body"] = converted
        fields = self.fields(data.get("list_fields") or {}, f"{where}.list_fields")
        if field_names:
            fields = {field_names.get(name, name): value for name, value in fields.items()}
        if fields:
            sample["fields"] = fields
        return sample, main_xpath

    def page_state(self, data: dict[str, Any], where: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in PAGE_STATE_SELECTOR_KEYS:
                result[key] = [
                    converted
                    for index, selector in enumerate(value or [])
                    if (converted := self.selector(selector, f"{where}.{key}[{index}]"))
                ]
            elif key == "reload_on":
                kept = [state for state in value if str(state).lower() not in DROPPED_RELOAD_STATES]
                if len(kept) != len(value):
                    self.warn(
                        f"{where}.reload_on: 'has_items' dropped, items mean the page is ready"
                    )
                result[key] = kept
            else:
                result[key] = value
        return result


class UnsupportedFormatError(ValueError):
    pass


def convert(name: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if "pages" in raw and "xpath_options" not in raw:
        raise UnsupportedFormatError(
            f"{name}: the 'options' + 'pages' prototype format is not supported, "
            "write scraper.json v3 by hand"
        )
    conversion = Conversion()
    conversion.unknown(
        "",
        raw,
        {
            "website",
            "links",
            "options",
            "navigation",
            "page_state",
            "page_discovery",
            "proxy_check",
            "detail_input_defaults",
            "xpath_options",
            "time_options",
            "scroll_options",
            "duplicate_observability",
            "login_data",
        },
    )
    result: dict[str, Any] = {"schema_version": 3, "name": name, "website": raw.get("website")}

    links = (raw.get("options") or {}).get("links") or raw.get("links") or []
    if links:
        result["links"] = [{"id": "main", "type": "list", "urls": links}]
    if raw.get("navigation"):
        result["navigation"] = raw["navigation"]

    page_state = raw.get("page_state") or {}
    conversion.unknown("page_state", page_state, {"common", "catalog", "detail"})
    if page_state.get("common"):
        result["page_state"] = conversion.page_state(page_state["common"], "page_state.common")

    discovery = raw.get("page_discovery") or {}
    if discovery:
        for key in sorted(set(discovery) - DISCOVERY_KEYS):
            conversion.warn(f"page_discovery.{key}: runtime setting, not part of scraper.json v3")
        result["discovery"] = {key: discovery[key] for key in DISCOVERY_KEYS if key in discovery}

    health = _convert_health(raw.get("proxy_check") or {}, conversion)
    if health is not None:
        result["health_check"] = health

    inputs = raw.get("detail_input_defaults") or {}
    if inputs:
        for key in sorted(set(inputs) - INPUT_KEYS):
            conversion.warn(f"detail_input_defaults.{key}: not converted")
        result["inputs"] = {key: inputs[key] for key in INPUT_KEYS if key in inputs}

    for key in ("duplicate_observability", "login_data"):
        if raw.get(key):
            conversion.warn(f"{key}: not converted, configure it manually")

    time_options = raw.get("time_options") or {}
    scroll_options = raw.get("scroll_options") or {}
    known_time = set(LIST_WAITS) | set(LIST_DELAYS) | set(DETAIL_WAITS) | set(DETAIL_DELAYS)
    known_time |= set(AUTH_WAITS) | set(AUTH_DELAYS) | OBSOLETE_TIME_OPTIONS
    conversion.unknown("time_options", time_options, known_time)
    conversion.unknown(
        "scroll_options",
        scroll_options,
        set(SCROLL_OFFSETS) | {"scroll_to_list_items", "scroll_to_detail_fields"},
    )

    xpath_options = raw.get("xpath_options") or {}
    conversion.unknown(
        "xpath_options", xpath_options, {"paginator", "list_page", "detail_page", "api_options"}
    )
    if xpath_options.get("api_options"):
        result["options"] = {"api": xpath_options["api_options"]}

    paginator = xpath_options.get("paginator") or {}
    list_templates = xpath_options.get("list_page") or []
    if isinstance(list_templates, dict):
        list_templates = [list_templates]
    pages: dict[str, Any] = {}
    list_page = _convert_list_page(
        list_templates,
        paginator,
        page_state.get("catalog"),
        time_options,
        scroll_options,
        conversion,
    )
    if list_page is not None:
        pages["list"] = list_page
    detail_page = _convert_detail_page(
        xpath_options.get("detail_page") or {},
        paginator,
        page_state.get("detail"),
        time_options,
        scroll_options,
        conversion,
    )
    if detail_page is not None:
        pages["detail"] = detail_page
    result["pages"] = pages
    return result, conversion.warnings


def _locators(data: dict[str, Any]) -> list[dict[str, str]]:
    locators = []
    xpath = data.get("RELATIVE_XPATH") or data.get("XPATH")
    if xpath:
        locators.append({"xpath": xpath})
    if data.get("CSS"):
        locators.append({"css": data["CSS"]})
    return locators


def dump_json(data: Any) -> str:
    """Indented JSON with short scalar lists such as ``[0, 0]`` kept on one line."""

    def render(value: Any, indent: int) -> str:
        pad = " " * indent
        inner = " " * (indent + 4)
        if isinstance(value, dict) and value:
            items = [
                f"{inner}{json.dumps(key, ensure_ascii=False)}: {render(item, indent + 4)}"
                for key, item in value.items()
            ]
            return "{\n" + ",\n".join(items) + f"\n{pad}}}"
        if isinstance(value, list) and value:
            if all(not isinstance(item, (dict, list)) for item in value):
                flat = json.dumps(value, ensure_ascii=False)
                if len(flat) + indent <= 100:
                    return flat
            items = [f"{inner}{render(item, indent + 4)}" for item in value]
            return "[\n" + ",\n".join(items) + f"\n{pad}]"
        return json.dumps(value, ensure_ascii=False)

    return render(data, 0) + "\n"


def _convert_health(proxy_check: dict[str, Any], conversion: Conversion) -> dict[str, Any] | None:
    health = proxy_check.get("health_check") or {}
    if isinstance(health, bool):
        health = {"enabled": health}
    if not proxy_check.get("enabled", health.get("enabled", False)):
        return None
    result: dict[str, Any] = {"enabled": bool(health.get("enabled", True))}
    if "blocking" in proxy_check or "blocking" in health:
        result["blocking"] = bool(proxy_check.get("blocking", health.get("blocking")))
    url = proxy_check.get("url") or health.get("url")
    if url:
        result["urls"] = [url]
    timeout = proxy_check.get("timeout_seconds") or health.get("timeout_seconds")
    if timeout:
        result["timeout_seconds"] = timeout
    for source, target in (
        ("min_items", "min_items"),
        ("fields", "required_fields"),
        ("required_fields", "required_fields"),
        ("detail_fields", "detail_required_fields"),
        ("detail_required_fields", "detail_required_fields"),
        ("check_all_proxies", "check_all_proxies"),
        ("max_sample_urls", "max_sample_urls"),
    ):
        if source in health:
            result[target] = health[source]
    if "check_detail" in health:
        check_detail = health["check_detail"]
        if isinstance(check_detail, bool):
            check_detail = "always" if check_detail else "never"
        result["check_detail"] = check_detail
    for key in ("browser_slow_mo", "use_profile", "use_network_result"):
        if key in proxy_check or key in health:
            conversion.warn(f"proxy_check.{key}: runtime setting, not part of scraper.json v3")
    return result


def _waits(time_options: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {
        target: time_options[source] for source, target in mapping.items() if source in time_options
    }


def _scroll(scroll_options: dict[str, Any], enabled_key: str) -> dict[str, Any]:
    scroll = _waits(scroll_options, SCROLL_OFFSETS)
    if scroll_options.get(enabled_key):
        scroll["enabled"] = True
    return scroll if scroll.get("enabled") or any(v != [0, 0] for v in scroll.values()) else {}


def _repeats(paginator: dict[str, Any]) -> dict[str, Any]:
    # The previous format checked repeats only when asked and, unless told
    # otherwise, took one shared item as a repeat.
    if not paginator.get("wait_for_changing_content"):
        return {"enabled": False}
    repeats: dict[str, Any] = {
        "match": "all" if paginator.get("stop_if_all_items_the_same") else "any"
    }
    if "max_the_same_content_pages" in paginator:
        repeats["confirmations"] = paginator["max_the_same_content_pages"]
    return repeats


def _convert_list_page(
    templates: list[dict[str, Any]],
    paginator: dict[str, Any],
    state: dict[str, Any] | None,
    time_options: dict[str, Any],
    scroll_options: dict[str, Any],
    conversion: Conversion,
) -> dict[str, Any] | None:
    if not templates:
        return None
    next_page = paginator.get("next_page_link")
    converted_templates = []
    for index, template in enumerate(templates):
        where = f"xpath_options.list_page[{index}]"
        conversion.unknown(
            where,
            template,
            {
                "template_name",
                "list_page_waiting_tag",
                "list_page_stop_tag",
                "first_item",
                "second_item",
            },
        )
        first, main_xpath = conversion.item_sample(template["first_item"], f"{where}.first_item")
        converted: dict[str, Any] = {"name": template.get("template_name") or f"list_{index + 1}"}
        for source, target in (("list_page_waiting_tag", "ready"), ("list_page_stop_tag", "stop")):
            if template.get(source):
                selector = conversion.selector(template[source], f"{where}.{source}")
                if selector is not None:
                    converted[target] = selector
        if main_xpath:
            converted["item"] = {"xpath": main_xpath}
        converted["first_item"] = first
        second, _ = conversion.item_sample(
            template.get("second_item") or {}, f"{where}.second_item"
        )
        if second:
            converted["second_item"] = second
        if next_page:
            selector = conversion.selector(next_page, "xpath_options.paginator.next_page_link")
            if selector is not None:
                converted["next_page"] = selector
        converted_templates.append(converted)

    pagination: dict[str, Any] = {}
    for source, target in (
        ("start_page", "start_page"),
        ("last_page", "last_page"),
        ("infinity_loading", "infinity_loading"),
    ):
        if source in paginator:
            pagination[target] = paginator[source]
    pagination["repeats"] = _repeats(paginator)
    by_url = paginator.get("by_url") or {}
    if by_url.get("change_by_url") and by_url.get("template"):
        pagination["url_template"] = by_url["template"].replace("{URL}", "{url}")

    page: dict[str, Any] = {"templates": converted_templates, "pagination": pagination}
    if paginator.get("detail_page_by_click"):
        page["open_detail_by_click"] = True
    if paginator.get("save_start_link"):
        page["save_start_link"] = True
    for key, value in (
        ("waits", _waits(time_options, LIST_WAITS)),
        ("delays", _waits(time_options, LIST_DELAYS)),
        ("scroll", _scroll(scroll_options, "scroll_to_list_items")),
    ):
        if value:
            page[key] = value
    if state:
        page["state"] = conversion.page_state(state, "page_state.catalog")
    return page


def _convert_detail_page(
    detail: dict[str, Any],
    paginator: dict[str, Any],
    state: dict[str, Any] | None,
    time_options: dict[str, Any],
    scroll_options: dict[str, Any],
    conversion: Conversion,
) -> dict[str, Any] | None:
    if not detail.get("detail_fields") and not detail.get("detail_properties"):
        return None
    where = "xpath_options.detail_page"
    conversion.unknown(
        where, detail, {"detail_page_waiting_tag", "detail_fields", "detail_properties"}
    )
    template: dict[str, Any] = {"name": "detail"}
    if detail.get("detail_page_waiting_tag"):
        selector = conversion.selector(
            detail["detail_page_waiting_tag"], f"{where}.detail_page_waiting_tag"
        )
        if selector is not None:
            template["ready"] = selector
    required: list[str] = []
    fields = conversion.fields(
        detail.get("detail_fields") or {}, f"{where}.detail_fields", required
    )
    if fields:
        template["fields"] = fields
    if required:
        template["required_fields"] = required
    groups = {}
    for group_name, group in (detail.get("detail_properties") or {}).items():
        group_where = f"{where}.detail_properties.{group_name}"
        conversion.unknown(
            group_where,
            group,
            {"list_body", "first_item", "second_item", "list_fields", "required"},
        )
        names = {"field_name": "name", "field_value": "value"}
        converted: dict[str, Any] = {}
        if group.get("list_body"):
            body = conversion.selector(group["list_body"], f"{group_where}.list_body")
            if body is not None:
                converted["body"] = body
        converted["first_item"], main_xpath = conversion.item_sample(
            group["first_item"], f"{group_where}.first_item", names
        )
        if main_xpath:
            converted["item"] = {"xpath": main_xpath}
        if group.get("second_item"):
            second, _ = conversion.item_sample(
                group["second_item"], f"{group_where}.second_item", names
            )
            if second:
                converted["second_item"] = second
        properties = {}
        for property_name, spec in (group.get("list_fields") or {}).items():
            conversion.unknown(
                f"{group_where}.list_fields.{property_name}",
                spec,
                {"field_name", "ATTRIBUTE", "VALUE_PREPARE"},
            )
            if set(spec) == {"field_name"}:
                properties[property_name] = spec["field_name"]
                continue
            converted_spec: dict[str, Any] = {"label": spec["field_name"]}
            if spec.get("ATTRIBUTE"):
                converted_spec["attribute"] = spec["ATTRIBUTE"]
            if spec.get("VALUE_PREPARE"):
                converted_spec["prepare"] = conversion.prepare(
                    spec["VALUE_PREPARE"],
                    f"{group_where}.list_fields.{property_name}.VALUE_PREPARE",
                )
            properties[property_name] = converted_spec
        converted["properties"] = properties
        if group.get("required"):
            converted["required"] = True
        groups[group_name] = converted
    if groups:
        template["property_groups"] = groups

    page: dict[str, Any] = {"templates": [template]}
    if "detail_start_item" in paginator:
        page["start_item"] = paginator["detail_start_item"]
    if "detail_last_item" in paginator:
        page["last_item"] = paginator["detail_last_item"]
    for key, value in (
        ("waits", _waits(time_options, DETAIL_WAITS)),
        ("delays", _waits(time_options, DETAIL_DELAYS)),
        ("scroll", _scroll(scroll_options, "scroll_to_detail_fields")),
    ):
        if value:
            page[key] = value
    if state:
        page["state"] = conversion.page_state(state, "page_state.detail")
    return page


def convert_package(source_dir: Path, target_dir: Path) -> list[str]:
    raw = json.loads((source_dir / SOURCE_FILE).read_text(encoding="utf-8"))
    if len(raw) != 1:
        raise ValueError(f"{source_dir / SOURCE_FILE} must contain exactly one scraper")
    ((_, body),) = raw.items()
    converted, warnings = convert(target_dir.name, body)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / TARGET_FILE).write_text(dump_json(converted), encoding="utf-8")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a scraper config to schema_version 3.")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("target_dir", type=Path)
    args = parser.parse_args()
    try:
        warnings = convert_package(args.source_dir, args.target_dir)
    except UnsupportedFormatError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"written {args.target_dir / TARGET_FILE} ({len(warnings)} warnings)")
    try:
        load_scraper_package(args.target_dir, variables=os.environ)
    except ScraperPackageError as error:
        print(f"error: the converted package does not validate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
