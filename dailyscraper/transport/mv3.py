"""Proxy authentication through a Manifest V3 extension, for persistent profiles.

Playwright's own proxy authentication is unreliable in a persistent context, so
the extension sets the proxy and answers its authentication challenge. The
browser must not navigate before the extension has applied the proxy:
otherwise the first requests leave through the machine's own address.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

from dailyscraper.domain import Proxy

LOCAL_BYPASS = ["localhost", "127.0.0.1", "::1"]

BACKGROUND = """
let proxyApplied = false;
let proxyApplyError = null;

function applyProxyConfig() {
  return new Promise((resolve) => {
    chrome.proxy.settings.set({value: proxyConfig, scope: "regular"}, () => {
      const lastError = chrome.runtime.lastError;
      proxyApplied = !lastError;
      proxyApplyError = lastError ? lastError.message : null;
      resolve(proxyApplied);
    });
  });
}

globalThis.__dsProxyReady = applyProxyConfig();
chrome.runtime.onInstalled.addListener(applyProxyConfig);
chrome.runtime.onStartup.addListener(applyProxyConfig);

chrome.webRequest.onAuthRequired.addListener(
  (details, callback) => {
    if (!proxyCredentials.username && !proxyCredentials.password) {
      callback({});
      return;
    }
    callback({authCredentials: proxyCredentials});
  },
  {urls: ["<all_urls>"]},
  ["asyncBlocking"]
);

globalThis.__dsGetProxyState = async () => {
  await globalThis.__dsProxyReady;
  return await new Promise((resolve) => {
    chrome.proxy.settings.get({incognito: false}, (details) => {
      resolve({ok: proxyApplied, error: proxyApplyError, details: details});
    });
  });
};
"""


class ProxyExtensionError(RuntimeError):
    pass


def write_extension(directory: Path, proxy: Proxy) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": 3,
        "name": "dailyscraper proxy auth",
        "version": "1.0.0",
        "permissions": ["proxy", "storage", "webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"},
    }
    config = {
        "mode": "fixed_servers",
        "rules": {
            "singleProxy": {"scheme": proxy.scheme, "host": proxy.host, "port": proxy.port},
            "bypassList": LOCAL_BYPASS,
        },
    }
    credentials = {"username": proxy.user or "", "password": proxy.password or ""}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (directory / "background.js").write_text(
        f"const proxyConfig = {json.dumps(config)};\n"
        f"const proxyCredentials = {json.dumps(credentials)};\n{BACKGROUND}",
        encoding="utf-8",
    )
    return directory.resolve()


def launch_args(directory: Path) -> list[str]:
    return [f"--disable-extensions-except={directory}", f"--load-extension={directory}"]


def wait_until_ready(
    context: BrowserContext, proxy: Proxy, *, timeout_ms: int, settle_ms: int
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000
    worker = next(iter(context.service_workers), None)
    if worker is None:
        try:
            worker = context.wait_for_event("serviceworker", timeout=timeout_ms)
        except Exception as error:
            raise ProxyExtensionError(
                "the proxy extension did not start; the browser may ignore --load-extension"
                " (branded Chrome does, use the bundled Chromium)"
            ) from error
    expected = {"host": proxy.host, "port": proxy.port, "scheme": proxy.scheme}
    observed: dict[str, Any] = {}
    while True:
        try:
            state = worker.evaluate("globalThis.__dsGetProxyState()")
            rules = ((state or {}).get("details") or {}).get("value", {}).get("rules", {})
            single = rules.get("singleProxy") or {}
            observed = {
                "host": single.get("host"),
                "port": single.get("port"),
                "scheme": single.get("scheme"),
            }
            if (state or {}).get("ok") and observed == expected:
                break
        except Exception as error:
            observed = {"error": type(error).__name__}
        if time.monotonic() >= deadline:
            raise ProxyExtensionError(f"the proxy extension did not apply {expected}: {observed}")
        time.sleep(0.1)
    if settle_ms:
        time.sleep(settle_ms / 1000)
    return observed
