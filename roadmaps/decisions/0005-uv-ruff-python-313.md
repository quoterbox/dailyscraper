# 0005. uv, ruff, Python 3.13

Статус: принято (2026-10-22)

## Контекст

Предшественники используют Pipenv (`Pipfile`, медленный lock, нестандартный
формат) и flake8 (в MVP даже не установлен в окружении). MVP закреплён на Python
3.13, в системе установлен 3.12.

## Решение

- Зависимости и окружение — **uv**: `pyproject.toml` (PEP 621), `uv.lock` в
  репозитории, dependency groups (PEP 735) для разработки.
- Версия Python — `.python-version` = `3.13`; uv ставит её сам
  (`uv python install`).
- Линтер и форматтер — **ruff**, настройки в `pyproject.toml`.
- Команды: `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run ruff format`;
  короткие алиасы в `Makefile`.

## Последствия

- uv пока в версиях 0.x. Защита от изменений поведения: закоммиченный
  `uv.lock` и фиксированная версия uv в CI.
- Браузер Playwright ставится отдельной командой
  (`uv run playwright install chromium`), когда появится транспорт.
