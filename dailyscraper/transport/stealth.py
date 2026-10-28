STEALTH_SCRIPT = """
Object.defineProperty(navigator, "webdriver", { get: () => undefined });
Object.defineProperty(navigator, "languages", { get: () => ["ru-RU", "ru", "en-US", "en"] });
Object.defineProperty(navigator, "platform", { get: () => "Win32" });
Object.defineProperty(navigator, "vendor", { get: () => "Google Inc." });
Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
window.navigator.permissions.query = (parameters) => (
    parameters.name === "notifications"
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);
"""
