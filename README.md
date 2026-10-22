# dailyscraper

Фреймворк для сбора каталогов и карточек товаров через браузер: параллельные
браузеры, распределение прокси, preflight и health-проверки, распознавание
блокировок, retry, stop/resume и надёжный экспорт.

Проект в разработке. Архитектура, решения и текущее состояние — в
[roadmaps/](roadmaps/README.md).

## Разработка

Нужен [uv](https://docs.astral.sh/uv/). Python 3.13 uv установит сам.

```bash
uv sync
uv run pytest
uv run ruff check .
```

Короткие команды — в `Makefile`: `make test`, `make lint`, `make format`.
Проверка доступности контролируемого сайта (нужна сеть): `make test-network`.
