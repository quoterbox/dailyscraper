# Статус

Обновлено: 2026-10-23. Ветка: `dev`.

## Готовые этапы

- **0 — каркас:** закоммичен.
- **1 — домен и схема:** закоммичен.

## Текущий этап: 2 — страницы в одном процессе

Готово, ожидает коммита владельцем.

### Сделано

- `transport/`: запуск Playwright (с изоляцией Node-драйвера от Ctrl+C на
  Windows), `BrowserPage`, `ElementFinder` (альтернативы селектора с общим
  ожиданием, поиск внутри элемента, `parent_text`), пробник страницы,
  минимальный stealth.
- `detection/`: маркеры по умолчанию (без маркеров конкретных сайтов),
  политика ожидания, классификатор состояния (чистая функция), цикл ожидания с
  перезагрузками, hook пакета и точками для ручного вмешательства и
  «активности»; единый классификатор ошибок.
- `execution/`: `Navigator` (warmup, открытие с повторами, bootstrap через
  hooks, fault injection навигации), `Pacer`, `ItemSink`/`JsonLinesSink`,
  загрузка `actions.py`/`pipeline.py` пакета.
- `actions/ScraperActions`, `page_scrapers/` (List с пагинацией по URL и по
  кнопке, infinity loading, правилом «та же страница»; Detail с таблицами
  свойств и проверкой полноты; Auth), `pipelines/DefaultPipeline`.
- `app/single.py`: временная команда «один парсер — один браузер», вывод NDJSON
  и CSV.
- Настройки: секция `browser` (`config/<env>/browser.json`).
- ADR [0011](decisions/0011-page-execution-contract.md).
- Зависимость `playwright==1.59.0` — проверенная версия MVP, браузер той же
  сборки уже установлен; обновление — отдельным шагом.

### Проверено

| Команда | Результат |
|---|---|
| `uv run pytest` | 172 passed, 3 deselected |
| `uv run python tools/test_suite.py browser` | 2 passed: каталог 9/9/1 (19 товаров, остановка на `last_page`); detail совпадает с `controlled_details_v1.json`, отсутствующий товар — `not_found` |
| `uv run python tools/test_suite.py network` | 1 passed |
| `uv run ruff check .`, `uv run ruff format --check .` | чисто |

Запуск вручную (testing-окружение):

```bash
DAILYSCRAPER_ENV=testing DAILYSCRAPER_TEST_WORKSPACE=.test-artifacts/manual \
  uv run python -m dailyscraper.app.single controlled_catalog
```

### Отличия от поведения MVP

- Значение свойства с `prepare` обрабатывается из ячейки значения (в MVP —
  ошибочно из ячейки названия).
- Переводы строк в тексте заменяются пробелом, а не склеиваются.
- Отсутствующее поле — `null`, а не строка `"None"`.
- Относительные `href`/`src` приводятся к абсолютным от адреса страницы.
- Элементы списка обходятся по индексу на странице, а не поиском каждого поля
  заново от тела элемента.

### Не входит в этап 2

Детали по клику, ручное вмешательство при CAPTCHA и «активность» (точки
расширения готовы), постоянные профили, блокировка ресурсов, proxy-auth.

## Следующий шаг: этап 3 — хранилище и выход

1. `storage/`: SQLite store (миграции, retry блокировок, одна запись за раз),
   таблицы `tasks`/`attempts`.
2. Immutable input snapshots и partitions с checkpoints вместо одного NDJSON.
3. Merge/export (CSV, JSON, XLSX, XML), summaries, финализация при любом исходе.
4. Перенос тестов контрактов output и финализации из MVP.
