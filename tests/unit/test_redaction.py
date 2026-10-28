from __future__ import annotations

from dailyscraper.observability import redact


def test_secret_keys_and_url_credentials_are_masked() -> None:
    value = {
        "proxy": "http://user:p4ss@10.0.0.1:8080",
        "api_key": "abc",
        "nested": [{"Password": "x", "name": "kept"}],
        "token": "",
        "count": 3,
    }

    assert redact(value) == {
        "proxy": "http://***@10.0.0.1:8080",
        "api_key": "***",
        "nested": [{"Password": "***", "name": "kept"}],
        "token": "",
        "count": 3,
    }


def test_plain_urls_are_unchanged() -> None:
    assert (
        redact("https://dailyscraper.com/tests/?q=a:b") == "https://dailyscraper.com/tests/?q=a:b"
    )
