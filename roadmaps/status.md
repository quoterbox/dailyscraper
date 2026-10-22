# Статус

Обновлено: 2026-10-22. Ветка: `dev`.

## Этап 0 — каркас

Готово и закоммичено.

## Текущий этап: 1 — домен и схема

Готово, ожидает коммита владельцем.

### Сделано

- `domain/`: `ErrorCategory`, `PageType`, `PageState`, `PageOutcome`.
- `pages/`: селекторы с упорядоченными альтернативами и `prepare`; один
  алгоритм вывода XPath элементов по двум образцам (по шагам пути, без
  ограничения на число цифр индекса, с поддержкой `/` в предикатах); модели
  `ListPage`, `DetailPage`, `AuthPage`, `PageStateRules`; компиляция templates в
  неизменяемые `Compiled*Template`.
- `schema/`: `ScraperDefinition` (`scraper.json` v3), загрузка пакета с
  подстановкой `${env:…}`, проверкой имени и компиляцией шаблонов.
- `settings/`: загрузка `config/<env>/*.json`, `*.local.json`, оверлей тестовой
  конфигурации, переопределения `DAILYSCRAPER__…`, `.env`; неизменяемый
  `Settings`; в testing данные и логи только внутри рабочей папки.
  Конфиги `config/development`, `config/testing`: секции `paths`, `scrapers`.
- `tools/convert_scraper.py`: перевод предыдущего формата в v3 с
  предупреждениями о непереносимом и проверкой результата.
- Контролируемые пакеты `tests/scrapers/controlled_catalog`,
  `tests/scrapers/controlled_details` в формате v3, полученные конвертером из
  `tests/fixtures/previous_format/`.
- ADR [0008](decisions/0008-scraper-json-v3.md),
  [0009](decisions/0009-scraper-actions-hooks.md),
  [0010](decisions/0010-settings-loading.md).
- Зависимости: `pydantic`, `python-dotenv`.

### Проверено

| Команда | Результат |
|---|---|
| `uv run pytest` | 116 passed, 1 deselected |
| `uv run python tools/test_suite.py network` | 1 passed |
| `uv run ruff check .`, `uv run ruff format --check .` | чисто |
| Конвертер на всех пакетах предыдущего формата | используемые пакеты конвертируются и проходят валидацию; пакеты с авторизацией и формата прототипа требуют ручной правки |

### Отклонения от плана этапа

- `Task`, `Attempt`, `ProxyId` не добавлены: появятся на этапах 3–5 вместе с
  потребителями.
- Схемы runtime и workload не добавлены: появятся на этапах 3–4.
- Класс `ScraperActions` не реализован: контракт зафиксирован в ADR 0009,
  реализация — на этапе 2 вместе с обёрткой страницы.

## Следующий шаг: этап 2 — страницы в одном процессе

1. `transport/`: Playwright-адаптер, нейтральная обёртка страницы,
   `ElementFinder` на селекторах v3.
2. `detection/`: page state и единый классификатор ошибок.
3. `execution/`: `Navigator` (warmup, bootstrap, ожидание состояния),
   `ItemSink` в файл.
4. `page_scrapers/`: List / Detail / Auth на скомпилированных шаблонах;
   `pipelines/DefaultPipeline`; `actions/ScraperActions`.
5. Временный CLI: один парсер, один браузер, без прокси. Критерий: controlled
   site — каталог 9/9/1 (19 товаров), detail совпадает с
   `tests/fixtures/expected/controlled_details_v1.json`.
