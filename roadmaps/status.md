# Статус

Обновлено: 2026-10-28. Ветка: `dev`.

## Готовые этапы

- **0 — каркас:** закоммичен.
- **1 — домен и схема:** закоммичен.
- **2 — страницы в одном процессе:** закоммичен. Transport на Playwright,
  распознавание состояния страницы, `Navigator`, исполнители List/Detail/Auth,
  `DefaultPipeline` — [0011](decisions/0011-page-execution-contract.md).

## Текущий этап: 3 — хранилище и выход

Основная часть готова, ожидает коммита владельцем.

### Сделано

- `domain/`: `RunRecord`/`RunStatus`, `BatchCommit`/`PartitionSeal`/
  `PartitionState`, `Event` с версией схемы, `DeliveryRecord`.
- `storage/`: интерфейсы `RunRepository`, `PartitionRegistry`, `EventLog`,
  `DeliveryLog`, `Store`; SQLite-адаптер (миграции по `user_version`, запись под
  `BEGIN IMMEDIATE`, повтор всей операции при занятой базе, точка fault
  injection); `RunLayout` — раскладка файлов запуска; чтение подтверждённых
  записей с проверкой контрольных сумм.
- Контрактные тесты хранилища (`tests/contract/`) — один набор для каждого
  бэкенда, сейчас для SQLite.
- `execution/partitions.py`: `PartitionWriter` (NDJSON-партиции, fsync, отметка
  после записи, откат хвоста при неудачной отметке, обрезка незарегистрированного
  хвоста, смена партиции по лимитам), `PartitionSink` (пакеты по числу строк и
  времени), `ItemIdentity` → `item_key`.
- Pipeline пишет строки по ходу работы: каталог — постранично, детали — по
  одной, с позицией во входном файле и статусом строки.
- `tasks/inputs.py`: неизменяемый снимок входного CSV с манифестом и
  контрольной суммой, проверка обязательных колонок, удаление дублей.
- `output/`: сборка итоговых строк (каталог — в порядке отметок; детали — в
  порядке входа, последний результат по строке), форматы CSV/JSON/XLSX/XML с
  атомарной записью, архив, доставка в папку с повторами (адаптер получателя).
- `observability/`: события запуска в хранилище, маскирование секретов.
- `app/run_service.py`: запуск с финализацией при любом исходе (успех, ошибки
  строк, сбой, Ctrl+C): закрытие партиций, экспорт, доставки, итоги,
  `summary.json`, статус, события `run.started`/`item.failed`/
  `delivery.finished`/`run.finished`.
- CLI `dailyscraper`: `run SCRAPER [--input CSV] [--headed]`, `runs`,
  `runs show RUN_ID`. Временная команда `app/single.py` удалена.
- Версия пакета парсера — хэш содержимого `.json` и `.py`.
- Настройки: `project_id`, секции `storage` и `output` (форматы, архив,
  доставки).
- Зависимость `openpyxl` для XLSX.

### Проверено

| Команда | Результат |
|---|---|
| `uv run pytest` | 233 passed (unit + contract), 5 deselected |
| `uv run python tools/test_suite.py browser` | 4 passed: каталог и детали напрямую и через `RunService` (CSV, история, итоги) |
| `uv run ruff check .`, `uv run ruff format --check .` | чисто |
| `dailyscraper run controlled_catalog`, `dailyscraper runs` | 19 строк, CSV, запись в истории |

Один из прогонов браузерных тестов однажды получил `load_failed` вместо
`not_found` для отсутствующего товара (ошибка тестового сайта); повторы прошли.
Причина таких строк теперь видна в событиях `item.failed`.

Запуск вручную (testing-окружение, PowerShell):

```powershell
$env:DAILYSCRAPER_ENV = "testing"
$env:DAILYSCRAPER_TEST_WORKSPACE = ".test-artifacts/manual"
uv run dailyscraper run controlled_catalog --headed
uv run dailyscraper runs
```

### Отличия от плана этапа 3

- Таблицы `tasks`/`attempts` переносятся на этап 4 вместе с очередью: без
  очереди их нечем наполнять; схема добавится миграцией.
- Хранилище артефактов — пока раскладка файлов запуска (`RunLayout`) на локальной
  ФС; интерфейс для S3 появится вместе со вторым бэкендом.
- Тесты output и финализации написаны заново под новую модель, а не перенесены
  из MVP дословно.
- CLI `run`/`runs` появился раньше этапа 8, потому что история запусков нужна
  уже сейчас; остальные команды — на этапе 8.

### Осталось по этапу 3

- Возобновление незавершённого запуска (reconciliation партиций по реестру) —
  вместе с очередью на этапе 4.
- Очистка старых запусков (retention) — этап 8.

## Решения после этапа 2

- ADR [0012](decisions/0012-transports-isolation-and-sync-core.md): транспорт за
  узким интерфейсом (Playwright и HTTP), HTTP-транспорт — этап 4a, режимы
  изоляции браузера `persistent` / `ephemeral` / `shared`, синхронное ядро и
  async-фасад для встраивания.
- ADR [0013](decisions/0013-scaling-and-replaceable-backends.md): дорожки
  HTTP-слота, `max_parallel` парсера, пакетные отметки, нагрузочные тесты;
  хранилище за интерфейсами с контрактными тестами (PostgreSQL, Redis, S3 —
  адаптерами), сериализуемый протокол слота, идемпотентность, `host_id`;
  координатор и агенты хостов — этап 10.
- ADR [0014](decisions/0014-captcha-solvers-and-ai-observer.md): ручное решение
  капчи с уведомлением на почту и в Telegram — этап 4b; автоматические решатели
  (сервисы, ИИ) — этап 6a в той же цепочке; ИИ-наблюдатель вне ядра с
  проверяемыми предложениями изменений `scraper.json` — этап 9.
- ADR [0015](decisions/0015-open-core-resources-and-runs.md): ядро — открытая
  библиотека с CLI; HTTP API и веб-интерфейс — поверх `app/`, вне ядра;
  ресурсный профиль 10–15 сайтов на 8 ГБ и 2 ядрах; история запусков,
  `item_key`, версия пакета — хэш; доставка через адаптеры получателей;
  `project_id`; Docker-образ и шаблон развёртывания — этап 8.
- Ближайшая цель — работающий сервис для одного владельца на одной машине
  (этапы 3–7).
- Защищённые пути разработки для тестовой рабочей папки читаются из
  `config/development`; чтение конфигов вынесено в `settings/sources.py`.

## Следующий шаг: этап 4 — параллельность

1. `TaskQueue` и таблицы `tasks`/`attempts` (миграция 2), planner: ссылки →
   `LIST`, вход → `DETAIL` диапазонами.
2. `BrowserManager`: процессы-слоты (spawn), протокол сообщений — версионируемые
   JSON-модели, пакетные `ItemsCommitted`, watchdog по `Progress`, kill одного
   слота, leases хоста, бюджет памяти.
3. `Dispatcher` в родителе: claim → слот → обработка событий → reschedule;
   `max_parallel` парсера; stop/cancel/kill.
4. Возобновление запуска: reconciliation партиций по реестру.
