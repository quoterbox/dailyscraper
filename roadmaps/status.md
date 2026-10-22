# Статус

Обновлено: 2026-10-22. Ветка: `dev`.

## Текущий этап: 0 — каркас

Готово.

### Сделано

- `roadmaps/`: [план](plan.md), [заметки](notes.md), ADR
  [0001](decisions/0001-clean-repository-and-naming.md)–[0006](decisions/0006-roadmaps-as-project-memory.md).
- Инструменты: `pyproject.toml` (uv, hatchling, dependency group `dev`: pytest,
  ruff, psutil), `uv.lock`, `.python-version` = 3.13, `Makefile`, `.gitignore`,
  `.editorconfig`. ruff обрабатывает только `*.py`/`*.pyi`.
- Пакет `dailyscraper/`: 20 пакетов-слоёв, пока пустых; тест правила
  зависимостей `tests/unit/test_layers.py`.
- `dailyscraper/settings/environment.py`: выбор окружения (`DAILYSCRAPER_ENV`)
  и правила изоляции testing-окружения.
- `dailyscraper/testing/fault_injection/`: детерминированное внедрение сбоев с
  точками под слоты: `navigation.before_open` / `after_open`,
  `slot.after_session_open` (контекст `task_kind`), `slot.after_task_started`.
- `tests/bootstrap.py`, `tests/conftest.py`: изоляция тестовой сессии в
  `.test-artifacts/<session>/`, рабочие папки сценариев с сохранением при
  падении.
- `tests/harness/`: управление процессами, локальный CONNECT-прокси, рабочие
  папки сценариев.
- `tests/fixtures/`: контракт controlled site.
- `tools/test_suite.py`: профили `fast`, `network`, `browser`, `full`.

### Проверено

| Команда | Результат |
|---|---|
| `uv run pytest` (профиль по умолчанию) | 45 passed, 1 deselected |
| `uv run python tools/test_suite.py network` | 1 passed: controlled site отдаёт 9/9/1 товаров, 19 уникальных |
| `uv run ruff check .`, `uv run ruff format --check .` | чисто |

### Отклонения от плана этапа

- Конфиги `config/<env>/` и их загрузка перенесены на этап 1: формат загрузчика
  зависит от схемы v3.
- Части harness, запускающие CLI и читающие SQLite, переносятся на этапах 3–4
  вместе с runtime.
- Тесты fault injection для браузерных точек вернутся на этапе 2 вместе с
  `transport/`.

## Следующий шаг: этап 1 — домен и схема

1. `domain/`: `TaskKind`, `Task`, `Attempt`, `ErrorCategory`, `PageState`,
   `PageOutcome`, `TaskOutcome`, `ProxyId`, `Selector` с альтернативами.
2. Модели страниц (`pages/`) с неизменяемым конфигом и `CompiledTemplate`;
   вычисление общего XPath с тестами.
3. `schema/`: `scraper.json` v3, runtime, workload; `settings/`: загрузка
   `config/<env>/*.json` и неизменяемый снимок.
4. ADR про hooks `ScraperActions` и формат selector alternatives.
5. `tools/convert_scraper.py` и контролируемые пакеты `tests/scrapers/` в
   формате v3.
