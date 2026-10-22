# План dailyscraper

Целевая архитектура, контракты компонентов, карта переноса и этапы.
Решения, на которых держится план, — в [decisions/](decisions/); текущее
состояние — в [status.md](status.md).

## 1. Цель и исходные материалы

dailyscraper — фреймворк для воспроизводимого сбора каталогов и карточек товаров
через браузер: параллельные браузеры, распределение прокси (в том числе
резидентских), preflight и health-проверки, распознавание блокировок, retry,
stop/resume и надёжный экспорт.

Собирается из двух предшественников ([0001](decisions/0001-clean-repository-and-naming.md)):

- **MVP** — предыдущая версия фреймворка. Даёт проверенную на долгих прогонах
  инфраструктуру: Playwright-транспорт, proxy-auth через MV3, профили, page state
  и маркеры блокировок, источники прокси и network check, SQLite store, leases,
  команды stop/cancel/kill, immutable inputs, partitions с checkpoints, export,
  recovery, уведомления, retention, fault injection, acceptance-harness.
- **Прототип** — даёт простую модель страниц: типизированные `ListPage`,
  `DetailPage`, `AuthPage` с упорядоченными templates, page-scraper на тип
  страницы, pipeline парсера и hooks, вычисление общего XPath по первому и
  второму item.

Код переносится по компонентам и адаптируется к правилам ниже; ни один модуль не
копируется целиком. Не переносятся как архитектура: планирование и исполнение
jobs, оркестрация run, ранняя привязка прокси к задачам, несколько моделей
запуска браузера.

## 2. Принципы

1. **Один владелец решения.** Порядок задач, выбор прокси, выделение браузера и
   retry решает только `Dispatcher` в родительском процессе.
2. **Позднее связывание.** Задача не знает о прокси и браузере; они записываются
   в attempt в момент запуска.
3. **Один исполнитель браузеров.** Обычные задачи, discovery и health-probe идут
   через `BrowserManager` в процессах-слотах, которые можно убить по отдельности
   ([0002](decisions/0002-sync-dispatcher-and-process-slots.md)).
4. **Один писатель SQLite.** Пишет только родитель; слоты пишут данные в
   partition-файлы и сообщают о коммите.
5. **Типизированные исходы.** `PageOutcome`, `TaskOutcome`, `ErrorCategory` —
   enum и dataclass, а не строки в dict. Классификатор один.
6. **Зависимости только вниз** (раздел 4.2), проверяется тестом.
7. **Конфигурация неизменяема на время run.** Снимок настроек передаётся слотам
   при старте, слоты не читают конфиг с диска.
8. **Чистые имена** ([0001](decisions/0001-clean-repository-and-naming.md)).

## 3. Схема

```
CLI: dailyscraper run | status | stop | resume | retry | rebuild | storage
 │
 ▼
RunService ── снимок конфига, старт/финал run, команды
 │
 ▼
Dispatcher (родитель, один поток, цикл событий)
 ├── TaskQueue        задачи + attempts в SQLite; claim / complete / reschedule
 ├── Gates            preflight-ворота: probe прошёл → задачи парсера доступны
 ├── ProxyManager     PoolSnapshot → Allocator → HealthRegistry (общий для хоста)
 ├── BrowserManager   процессы-слоты, лимиты хоста (leases), watchdog, kill
 └── RetryPolicy / ErrorClassifier (чистые функции)
        │  pipe: RunTask / Stop / Cancel ▼     ▲ Started / Progress / ItemsCommitted / Finished / Died
        ▼
Slot-процесс (один Chromium на пару site+proxy, много задач)
 └── SlotRuntime
      ├── Navigator       open → bootstrap/warmup → PageStateDetector → intervention
      ├── Pipeline        DefaultPipeline | pipeline.py пакета
      │    └── PageScrapers: Auth / List / Detail (+ стратегии)
      ├── ScraperActions  hooks пакета
      └── ItemSink        partition writer + fsync → ItemsCommitted
```

## 4. Структура пакета и слои

### 4.1 Пакеты

```
dailyscraper/
  domain/         ids, tasks, outcomes, errors, selectors, proxy — чистые типы без I/O
  settings/       окружение, загрузка config/<env>/*.json, неизменяемый снимок
  schema/         модели scraper.json v3, runtime, workload
  pages/          модели страниц: Page, ListPage, DetailPage, AuthPage        ← SDK
  page_scrapers/  исполнители List / Detail / Auth, registry, стратегии
  pipelines/      DefaultPipeline, PipelineContext                            ← SDK
  actions/        ScraperActions (hooks)                                      ← SDK
  execution/      SlotRuntime, Navigator, ItemSink, CompiledTemplate
  detection/      page state, ErrorClassifier                                 ← SDK (маркеры)
  transport/      Playwright-адаптер, ElementFinder, interventions
  browsers/       BrowserManager, Slot, точка входа слота, сессии, профили, leases, watchdog, termination
  proxies/        sources, PoolSnapshot, Allocator, HealthRegistry, network check
  tasks/          TaskQueue, planner, discovery, RetryPolicy, inputs
  dispatch/       Dispatcher, Gates, fairness
  storage/        SQLite store (repositories, migrations, commands), data_path, partitions, checkpoints
  output/         merge, форматы, summaries
  app/            RunService, CLI, finalization, recovery
  observability/  события, консоль, уведомления
  maintenance/    storage retention
  testing/        fault injection (активна только в testing-окружении)
config/<env>/     JSON-настройки (development, testing)
scrapers/         сабмодуль с пакетами парсеров
tools/            утилиты разработки (test_suite, конвертер пакетов)
tests/            unit, integration, acceptance, harness, fixtures
```

Публичный SDK для пакетов парсеров — `dailyscraper.actions`, `dailyscraper.pages`,
`dailyscraper.pipelines`, `dailyscraper.detection`. Всё остальное внутреннее.

### 4.2 Правило зависимостей

Слои сверху вниз; модуль может импортировать только свой слой и ниже:

| Уровень | Пакеты |
|---|---|
| 7 | `app` |
| 6 | `dispatch`, `maintenance` |
| 5 | `tasks`, `proxies`, `browsers`, `output`, `observability` |
| 4 | `storage` |
| 3 | `execution`, `pipelines`, `page_scrapers`, `actions` |
| 2 | `transport`, `detection` |
| 1 | `schema`, `pages`, `settings`, `testing` |
| 0 | `domain` |

Дополнительно:

- `execution` и всё ниже не знают о `dispatch`, `proxies`, `tasks`, `storage` —
  они работают внутри слота;
- `storage` не импортирует `browsers`, `observability`, `app`.

Правило проверяет `tests/unit/test_layers.py` (AST-разбор импортов).

## 5. Доменная модель (`dailyscraper/domain`)

```python
class TaskKind(StrEnum):
    DISCOVER = "discover"  # найти последнюю страницу каталога → породить LIST
    LIST = "list"  # ссылка × диапазон страниц
    DETAIL = "detail"  # диапазон строк input snapshot (row_id)
    PROBE = "probe"  # health-проверка парсера через конкретную прокси


@dataclass(frozen=True)
class Task:
    task_id: str  # стабилен между attempts
    run_id: str
    scraper: str
    site_key: str
    kind: TaskKind
    payload: ListPayload | DetailPayload | DiscoverPayload | ProbePayload
    priority: int = 0
    not_before: float = 0.0
    affinity: ProxyAffinity | None = None


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    task_id: str
    attempt_no: int
    proxy_id: ProxyId | None
    slot_id: str
    output_dir: Path  # зависит от task_id/attempt_no, не от прокси


class ErrorCategory(StrEnum):
    # captcha, blocked, proxy_auth, rate_limited, timeout, network, load_failed,
    # blank, not_found, browser_closed, invalid_content, filesystem, cancelled, internal
    CAPTCHA = "captcha"
    BLOCKED = "blocked"
    ...


class PageState(StrEnum):
    # ready, empty, not_found, captcha, blocked, proxy_auth, load_failed, blank, unknown
    READY = "ready"
    EMPTY = "empty"
    ...


@dataclass(frozen=True)
class PageOutcome:
    state: PageState
    reason: str
    evidence: Mapping[str, str]  # http_status, final_url, marker, selector…
    items_count: int | None = None


@dataclass(frozen=True)
class TaskOutcome:
    status: Literal["success", "partial", "empty", "failed", "cancelled"]
    category: ErrorCategory | None
    items_committed: int
    pending: RowRange | PageRange | None  # что осталось при retry
    spawned: tuple[TaskSpec, ...] = ()  # DISCOVER → LIST, LIST → DETAIL
    pages: tuple[PageOutcome, ...] = ()


@dataclass(frozen=True)
class ProxyId:  # стабилен между run: id провайдера или hash(scheme, host, port, user без сессии)
    value: str
    generation: int = 0  # поколение резидентской сессии (раздел 6.2)
```

## 6. Компоненты

### 6.1 TaskQueue (`tasks/queue.py`)

```python
class TaskQueue(Protocol):
    def enqueue(self, specs: Iterable[TaskSpec]) -> None: ...
    def claim(self, *, now: float, eligible: Callable[[Task], bool]) -> Task | None: ...
    def start(self, task: Task, proxy: ProxyId | None, slot_id: str) -> Attempt: ...
    def complete(self, attempt: Attempt, outcome: TaskOutcome) -> None: ...
    def reschedule(
        self,
        attempt: Attempt,
        outcome: TaskOutcome,
        *,
        not_before: float,
        avoid: frozenset[ProxyId],
    ) -> None: ...
    def stats(self) -> QueueStats: ...
```

- Таблицы `tasks` и `attempts` в SQLite store, миграции — механизм из MVP.
- Retry — `reschedule` с `not_before` и `avoid`, без раундов и барьеров.
  `pending` сужает диапазон (item-level checkpoints).
- Fan-out: `TaskOutcome.spawned` попадает в очередь атомарно вместе с `complete`.
- Fairness в `claim`: weighted round-robin по парсерам, затем по ссылкам;
  recovery с повышенным приоритетом и aging против голодания.

### 6.2 ProxyManager (`proxies/`)

```python
class ProxyPoolSnapshot:  # разрешается один раз на run
    def for_scraper(self, scraper: str) -> Sequence[Proxy]: ...


class ProxyAllocator(Protocol):
    def acquire(
        self,
        site_key: str,
        scraper: str,
        *,
        avoid: frozenset[ProxyId] = frozenset(),
        affinity: ProxyAffinity | None = None,
    ) -> ProxyGrant | WaitUntil: ...
    def release(self, grant: ProxyGrant) -> None: ...


class ProxyHealth(Protocol):  # общий для хоста: SQLite + TTL
    def report(
        self, site_key: str, proxy: ProxyId, category: ErrorCategory | None
    ) -> HealthTransition: ...
    def state(self, site_key: str, proxy: ProxyId) -> HealthState: ...
```

- Источники `static` / `gateway` / `api` из MVP, со стабильным `ProxyId` (не
  позиционный `api_{index}`) и явной солью сессии вместо переменной окружения.
- `Allocator` — единственное место выбора прокси. Политика: health (healthy >
  unknown > degraded > half-open) → наименьшая недавняя нагрузка → `avoid`
  из прошлых attempts.
- `HealthRegistry` — circuit breaker из MVP, но
  хранение общее для хоста с TTL, запись вне lock, half-open = одна задача.
- **Резидентские сессии.** `ProxyId` слота gateway стабилен (`res_01`…), сессия
  задаётся `generation`. Ротация увеличивает `generation` по TTL провайдера или
  при открытии circuit. Health хранится по `(site, ProxyId, generation)`.
  Профиль браузера привязан к `(site, ProxyId)`, при ротации — политика
  `reset | keep` (по умолчанию `reset` для blocked, `keep` для TTL;
  уточняется к этапу 5).

### 6.3 BrowserManager (`browsers/`)

```python
class BrowserManager(Protocol):
    def capacity(self) -> int: ...
    def slot_for(self, site_key: str, grant: ProxyGrant | None) -> Slot | WaitForCapacity: ...
    def submit(self, slot: Slot, task: Task, attempt: Attempt, deadline: float) -> None: ...
    def poll(self, timeout: float) -> list[SlotEvent]: ...
    def retire(self, slot_id: str, reason: str) -> None: ...  # после текущей задачи
    def kill(self, slot_id: str, reason: str) -> None: ...  # дерево процессов
    def shutdown(self, grace: float) -> None: ...
```

- Собственные `multiprocessing.Process` (spawn). Слот держит одну
  Playwright-сессию на пару `(site_key, ProxyId)`; ротация сессии по
  `max_age` / `max_jobs` / `max_items`.
- `kill` убивает только этот слот и его дерево (логика из MVP).
- Watchdog — по событиям `Progress` из pipe.
- Leases хоста (браузеры всего, site+proxy, профиль) берутся на время жизни
  слота. Лимиты — настройка хоста в SQLite, а не значения из запроса.
- Слот не открывает SQLite и не читает конфиг с диска; точка входа —
  `browsers/slot_main.py`.

| Родитель → слот | Слот → родитель |
|---|---|
| `RunTask(task, attempt, proxy, deadline)` | `TaskStarted(attempt_id)` |
| `Stop(grace)` / `Cancel()` | `Progress(attempt_id, phase, url, items)` |
| `Retire()` | `ItemsCommitted(attempt_id, partition, byte_range, row_ids)` |
| | `TaskFinished(attempt_id, TaskOutcome)` |
| | `SessionBroken(reason)`; смерть процесса → `SlotDied(exitcode)` |

### 6.4 Dispatcher (`dispatch/dispatcher.py`)

```python
while not stop_requested and queue.has_work():
    while browsers.capacity() > 0:
        task = queue.claim(now, eligible=gates.is_open)
        if task is None:
            break
        grant = proxies.acquire(
            task.site_key, task.scraper, avoid=history.avoid(task), affinity=task.affinity
        )
        if isinstance(grant, WaitUntil):
            queue.defer(task, grant.at)
            continue
        slot = browsers.slot_for(task.site_key, grant)
        if isinstance(slot, WaitForCapacity):
            queue.defer(task, slot.at)
            proxies.release(grant)
            break
        attempt = queue.start(task, grant.proxy_id, slot.id)
        browsers.submit(slot, task, attempt, deadline=policy.deadline(task))
    for event in browsers.poll(timeout=poll_interval):
        handle(event)  # checkpoint → classify → health.report → complete/reschedule → gates.update
    commands.poll()  # stop / cancel / kill
```

`handle(SlotDied)`: текущая задача получает `BROWSER_CLOSED`, незавершённая часть
вычисляется по закоммиченным строкам. Семантика stop/cancel/kill и grace — из MVP.

### 6.5 Gates и preflight (`dispatch/gates.py`)

- Network check (HTTP через `requests`) при сборке `PoolSnapshot` даёт начальный
  health.
- Health-probe — обычная задача `PROBE` в обычном слоте.
- Ворота на каждый парсер: задачи парсера доступны после первого `ready`-probe.
  Остальные прокси — `unknown`, их первая задача служит ленивой проверкой.
  Глобального барьера нет.
- Пороги блокировки run (`min_healthy_*`) — правило над результатами probe.

### 6.6 Распознавание блокировок (`detection/`)

`PageStateDetector` — цепочка сигналов, первый уверенный побеждает:

1. Транспорт: исключение навигации, HTTP-статус основного документа (407 / 403 /
   429 — структурно, не подстрокой), финальный URL и редиректы.
2. DOM: ready/counted selectors, маркеры captcha/blocked/empty/not-found из
   `page_state` (уровень `common` и тип страницы).
3. Hook пакета `classify_page_state(page_type, outcome)` — site-specific маркеры
   живут только здесь.

| Категория | Retry | Health прокси | Слот |
|---|---|---|---|
| `captcha` | другая прокси (или ручное вмешательство) | circuit open сразу | retire |
| `blocked`, `rate_limited` | другая прокси, backoff | circuit open сразу | retire |
| `proxy_auth` | другая прокси | circuit open, карантин на run | kill |
| `timeout`, `network`, `load_failed`, `blank` | да, по порогу | счётчик подряд → open | restart сессии по бюджету |
| `browser_closed` | да, та же прокси допустима | не влияет | restart |
| `empty` | одно подтверждение другой прокси | не влияет | — |
| `not_found` | нет (терминально для item) | не влияет | — |
| `filesystem`, `internal` | нет | не влияет | — |

### 6.7 Исполнение в слоте (`execution/`, `page_scrapers/`, `pipelines/`)

```python
class PageContext(Protocol):
    page: BrowserPage          # нейтральная обёртка над Playwright Page
    finder: ElementFinder      # find / find_all / find_inside
    navigator: Navigator       # open(url, page_type) -> PageOutcome
    sink: ItemSink             # emit(item); commit() → ItemsCommitted
    actions: ScraperActions
    progress: ProgressReporter
    cancel: CancelToken

class DefaultPipeline:
    def run(self, ctx: PipelineContext, task: Task) -> TaskOutcome:
        # auth? → list(range) → [detail inline при open_detail_by_click]; иначе spawn DETAIL
```

- Page-scrapers прототипа получают проверенные алгоритмы MVP: обход страниц
  каталога, load-more и infinity loading, итерацию detail по `row_id` с
  checkpoint и проверкой качества, авторизацию.
- `Navigator` объединяет warmup и bootstrap сессии, ожидание page state и ручное
  вмешательство при CAPTCHA.
- Пакет парсера переопределяет только `pipeline.py` и hooks; прокси, перезапуск
  браузера, SQLite и retry ему недоступны.
- **Detail: fan-out по умолчанию.** LIST коммитит строки и порождает DETAIL
  диапазонами `row_id`. Inline — только при `open_detail_by_click` или явном
  флаге.

## 7. Жизненный цикл run

1. `RunService` делает снимок конфига и резервирует `run_id`.
2. Startup reconciliation осиротевших run.
3. `PoolSnapshot`: источники → дедупликация → network check → начальный health.
4. Planner: каталог → `DISCOVER`/`LIST`; input → `DETAIL` по диапазонам
   immutable snapshot; `PROBE` для ворот.
5. `Dispatcher` работает до исчерпания очереди или terminal-команды.
6. Финализация **при любом исходе**: merge/export, summaries, уведомления,
   retention, terminal-статус run и команд.

Resume/retry/rebuild — новый run из задач и attempts исходного.

## 8. Конфигурация

`config/<env>/*.json`, окружение выбирает `DAILYSCRAPER_ENV`
(`development` по умолчанию, `testing` для тестов).

| Файл | Содержание |
|---|---|
| `paths.json` | Каталоги данных, логов, профилей, runtime |
| `logger.json` | Логирование |
| `browser.json` | Playwright, профили, proxy-auth, stealth, блокировка ресурсов |
| `runtime.json` | Dispatch (fairness, лимиты слотов), retry, health, watchdog, команды |
| `proxies.json` | Схема v2 MVP (static/gateway/api) + `id_strategy`, `gateway.session_rotation` |
| `proxy_check.json` | Network check и пороги ворот |
| `scrapers.json` | Выбранные парсеры, aliases, подмножество прокси |
| `workload.json` | Segments: link group × диапазоны list/detail; `proxy_id` — только affinity |
| `output.json` | Форматы и путь итогового экспорта |
| `storage.json` | Retention |

`scraper.json` v3 ([0003](decisions/0003-separate-scrapers-repository.md)):

```json
{
  "schema_version": 3,
  "name": "example_catalog",
  "website": "https://example.com",
  "links": [{"id": "main", "type": "list", "urls": []}],
  "navigation": {"warmup_urls": [], "open_target_after_warmup": true},
  "health_check": {"url": "…", "min_items": 1, "required_fields": []},
  "discovery": {"enabled": true, "empty_confirmations": 2},
  "inputs": {"detail_link_field": "detail_link", "identity_fields": []},
  "page_state": {"common": {}},
  "pages": {
    "list":   {"state": {}, "templates": []},
    "detail": {"state": {}, "templates": []},
    "auth":   {"templates": []}
  }
}
```

Selector alternatives: `{"selectors": [{"css": "…"}, {"xpath": "…"}]}`.

## 9. CLI

Одна точка входа `dailyscraper` (`[project.scripts]`) с подкомандами `run`,
`status`, `stop`, `resume`, `retry`, `rebuild`, `storage`. CLI только вызывает
сервисы `app/` — это условие для будущего HTTP API.

## 10. Итоговые свойства параллельности

| Свойство | MVP | dailyscraper |
|---|---|---|
| Модели запуска браузера | 3 | 1 (слоты) |
| Убить зависший браузер | весь пул, соседи теряют работу | один слот |
| Запуск Chromium | на каждую пачку jobs | на слот, ротация по лимитам сессии |
| Привязка прокси | при планировании | при запуске attempt |
| Выбор прокси | 5 алгоритмов | `ProxyAllocator` |
| Retry | раунды с барьером | `reschedule(not_before, avoid)` |
| Health прокси | на run, в памяти | на хост, SQLite + TTL |
| Писатели SQLite | родитель + каждый воркер | только родитель |
| Preflight | барьер для всех парсеров | ворота на парсер |
| Лимит браузеров | из запроса | настройка хоста |

## 11. Перенос из MVP по компонентам

| Компонент MVP | Решение | Куда |
|---|---|---|
| Playwright-транспорт, MV3 proxy-auth, stealth, блокировка ресурсов, поиск элементов | перенос, адаптация к `Selector` v3 | `transport/` |
| Профили браузера, очистка кэша, termination дерева процессов | перенос | `browsers/` |
| Сессии, bootstrap, warmup, ожидание page state, ручное вмешательство | адаптация | `execution/`, `transport/` |
| Page state и маркеры блокировок | перенос | `detection/` |
| Классификация ошибок (job, health, навигация) | объединение в один классификатор | `detection/` |
| Источники прокси, network check | перенос + стабильный `ProxyId`, поколения сессий | `proxies/` |
| Runtime circuit breaker | логика сохраняется, хранение — общее для хоста | `proxies/health.py` |
| Preflight-пороги | правила ворот | `dispatch/gates.py` |
| Scraper health mini-run | исполнитель задачи `PROBE` | `execution/` |
| Page discovery (алгоритм поиска последней страницы) | перенос | `tasks/discovery.py` |
| Планирование диапазонов, решение о retry | чистые функции | `tasks/` |
| Планировщик раундов, пул воркеров, перепривязка jobs к прокси, оркестрация | **не переносятся** | заменяются `Dispatcher`, `BrowserManager`, `TaskQueue` |
| SQLite store, миграции, retry блокировок, команды, leases, reconciliation | перенос + таблицы `tasks`/`attempts`, композиция репозиториев | `storage/`, `browsers/leases.py` |
| Immutable inputs, data paths, partitions, detail records | перенос | `storage/`, `tasks/inputs.py` |
| Writers, merge, export, summaries | перенос | `output/` |
| Recovery, finalization | адаптация под tasks/attempts | `app/` |
| Уведомления, read model, консоль | перенос; консоль — заново поверх read model | `observability/` |
| Storage retention | перенос | `maintenance/` |
| Fault injection | перенесено на этапе 0, точки под слоты | `testing/fault_injection/` |

Тесты harness переносятся по мере появления runtime: на этапе 0 — независимые
части, на этапах 3–4 — CLI-контроллер, ожидание состояния в SQLite и наложение
конфигов на рабочие папки сценариев. Acceptance-сценарии controlled site — на
том этапе, где появляется проверяемая функция.

## 12. Этапы

| # | Этап | Готово, когда |
|---|---|---|
| 0 | **Каркас.** `roadmaps/`, `pyproject.toml` (uv, ruff, pytest), пакеты слоёв, тест правила зависимостей, `settings/environment.py`, изоляция тестовой сессии, fault injection, `tests/harness` (process, CONNECT-прокси, workspace артефактов), fixtures controlled site, `tools/test_suite.py` | `uv run pytest` и `uv run ruff check` зелёные; controlled site отвечает в opt-in тесте `network` |
| 1 | **Домен и схема.** `domain/`, `schema/` (scraper.json v3, runtime, workload), `settings/` (загрузка `config/<env>`, снимок), модели страниц с `CompiledTemplate`, selector alternatives, `ErrorCategory`/`PageOutcome`; конвертер `tools/convert_scraper.py` | Контролируемые пакеты загружаются; тесты вычисления общего XPath проходят; конвертер переводит контролируемые пакеты из предыдущего формата |
| 2 | **Страницы в одном процессе.** `transport/` (Playwright, ElementFinder), `detection/`, Navigator, page-scrapers List/Detail/Auth с алгоритмами MVP, DefaultPipeline, ItemSink в файл; CLI без очереди: один парсер, один браузер, без прокси | Controlled site: каталог 3 страницы = 9/9/1, 19 товаров; detail из `controlled_details_v1.csv` совпадает с `controlled_details_v1.json` |
| 3 | **Хранилище и выход.** Store + `tasks`/`attempts`, data_path, partitions + checkpoints, merge/export, финализация при любом исходе | Контракты output и финализации из MVP проходят |
| 4 | **Параллельность.** TaskQueue, BrowserManager (слоты, pipe, watchdog, kill одного слота, leases хоста), Dispatcher, stop/cancel/kill | Перенесены и проходят acceptance: browser close, worker crash, interrupt, parent loss, multi run; убийство слота не трогает соседей |
| 5 | **Прокси.** Sources, PoolSnapshot, `ProxyId`, Allocator, health на хост, профили, MV3, резидентские сессии | Тесты прокси и health store проходят; соседний run видит открытый circuit; acceptance с CONNECT-прокси (407 → другая прокси) |
| 6 | **Ворота, discovery, probe.** `PROBE`/`DISCOVER`, ворота на парсер, пороги run | Парсер A работает, пока B на probe; planning stop и discovery recovery проходят |
| 7 | **Боевые парсеры.** Репозиторий парсеров, первые боевые пакеты (`catalog → detail`, API-режим, input-режим); парный прогон с MVP | Совпадение row_id/полей с MVP ≥ согласованного порога; многочасовой прогон без вмешательства |
| 8 | **Паритет эксплуатации.** CLI целиком, уведомления, консоль, retention, документация | MVP можно отключить |

Этапы 2 и 3 можно вести параллельно; этап 5 зависит от 4.

## 13. Принятые решения

| Вопрос | Решение |
|---|---|
| Sync или async | Синхронный Dispatcher, async внутри слотов при необходимости — [0002](decisions/0002-sync-dispatcher-and-process-slots.md) |
| HTTP-транспорт, FastAPI | Отложены до этапа 8+ — [0002](decisions/0002-sync-dispatcher-and-process-slots.md) |
| Репозиторий и имена | Новый `dailyscraper`, чистые имена — [0001](decisions/0001-clean-repository-and-naming.md) |
| Пакеты парсеров | Отдельный новый репозиторий, формат v3, одноразовый конвертер — [0003](decisions/0003-separate-scrapers-repository.md) |
| Ветки | `dev` → `master` по завершении этапа — [0004](decisions/0004-branches-and-git-ownership.md) |
| Инструменты | uv, ruff, Python 3.13 — [0005](decisions/0005-uv-ruff-python-313.md) |
| P0-баги MVP | Исправлены в MVP (407, финализация run, падение парсера, Noop store, закрытие SQLite); в новом коде закрываются конструкцией |
