# 0009. Hooks `ScraperActions`

Статус: принято (2026-10-22); сигнатуры с контекстом уточняются на этапе 2

## Контекст

Предшественники используют набор методов `actions_*`. Часть из них фреймворк
вызывает через `getattr`, не объявляя в базовом классе, а `after_add_item`
получает и возвращает весь список элементов.

## Решение

Базовый класс `ScraperActions` объявляет все hooks явно, без префикса
`actions_`. Каждый hook по умолчанию ничего не меняет.

| Hook | Когда вызывается | Предыдущее имя |
|---|---|---|
| `before_run()` / `after_run()` | начало и конец работы сессии над задачей | `actions_before_run` / `actions_after_run` |
| `bootstrap_version() -> str \| None` | версия подготовки сессии (cookies, регион) | `actions_bootstrap_version` |
| `validate_bootstrap(url) -> bool \| None` | проверка, что подготовка ещё действительна | `actions_validate_bootstrap` |
| `bootstrap(url) -> bool` | подготовка сессии | `actions_bootstrap` |
| `probe_bootstrap_state() -> PageOutcome \| None` | состояние страницы во время подготовки | `actions_probe_bootstrap_page_state` |
| `filter_links(links) -> links` | перед обходом ссылок | `actions_filter_links` |
| `before_login(url)` / `after_login()` | вокруг авторизации | `actions_before_login` / `actions_after_login` |
| `after_first_open(url)` | после первого открытия ссылки | `actions_after_first_loading` |
| `before_list(url)` / `after_list(items)` | вокруг обработки страницы списка | `actions_before_open_list_page` / `actions_after_open_list_page` |
| `prepare_list_item(item, url) -> item \| None` | перед сохранением элемента списка; `None` отбрасывает элемент | `actions_before_add_item`, `actions_after_add_item` |
| `before_detail(item)` / `after_detail(item)` | вокруг обработки detail-страницы | `actions_before_open_detail_page` / `actions_after_open_detail_page` |
| `classify_page_state(page_type, outcome) -> outcome` | после встроенного распознавания состояния | `actions_classify_page_state` |
| `prepare_output(items) -> items` | перед записью результата | `actions_prepare_output_items` |
| `collect_list(url, context) -> items \| None` | собственный сбор списка (например, через API) | `actions_collect_list_items` |
| `collect_from_input(items, context) -> items \| None` | собственный сбор по входным строкам | `actions_collect_list_from_input` |
| `discover_pages(context) -> result \| None` | собственный поиск последней страницы | `actions_discover_pages` |
| `run_warnings() -> list` | предупреждения пакета для итогового отчёта | `actions_get_run_warnings` |

## Последствия

- Пакет видит браузер через нейтральную обёртку страницы и `ElementFinder`, а
  не через объекты Playwright напрямую.
- Hooks не управляют прокси, перезапуском браузера, retry и хранилищем.
- `prepare_list_item` заменяет пару `before_add_item`/`after_add_item`: работа
  со всем списком переносится в `after_list` и `prepare_output`.
