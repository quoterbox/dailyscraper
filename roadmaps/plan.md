# План dailyscraper

Целевая архитектура, контракты компонентов, карта переноса и этапы.
Решения, на которых держится план, — в [decisions/](decisions/); текущее
состояние — в [status.md](status.md).

## 1. Цель и исходные материалы

dailyscraper — фреймворк для воспроизводимого сбора каталогов и карточек товаров
через браузер или HTTP: параллельные слоты, распределение прокси (в том числе
резидентских), preflight и health-проверки, распознавание блокировок, retry,
stop/resume и надёжный экспорт.

Ядро — открытая библиотека с CLI; HTTP API, веб-интерфейс и облачные надстройки
строятся поверх сервисов `app/` и в ядро не входят ([0015](decisions/0015-open-core-resources-and-runs.md)). Основной сценарий —
одна машина: 10–15 одновременных сайтов на 8–16 ГБ памяти и 2–4 ядрах.

Фреймворк рассчитан на эксплуатацию с высокой нагрузкой и отказоустойчивостью:
от одной машины до нескольких, если одной не хватает, с заменой SQLite и локальных файлов на
PostgreSQL, Redis и S3-совместимое хранилище без переписывания логики
([0013](decisions/0013-scaling-and-replaceable-backends.md)). Решение капч через API и ИИ-наблюдатель подключаются как расширения
([0014](decisions/0014-captcha-solvers-and-ai-observer.md)).

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
4. **Один писатель хранилища.** В общее хранилище пишет только родитель
   (на нескольких машинах — координатор и агенты хостов); слоты пишут данные в
   partition-файлы и сообщают о коммите пачками.
5. **Типизированные исходы.** `PageOutcome`, `TaskOutcome`, `ErrorCategory` —
   enum и dataclass, а не строки в dict. Классификатор один.
6. **Зависимости только вниз** (раздел 4.2), проверяется тестом.
7. **Конфигурация неизменяема на время run.** Снимок настроек передаётся слотам
   при старте, слоты не читают конфиг с диска.
8. **Чистые имена** ([0001](decisions/0001-clean-repository-and-naming.md)).
9. **Сменные бэкенды.** Очередь, run, команды, leases, health, события и
   артефакты — за интерфейсами с контрактными тестами; SQLite и локальные файлы —
   первые реализации ([0013](decisions/0013-scaling-and-replaceable-backends.md)).
10. **Сериализуемые сообщения и идемпотентность.** Протокол слота и события —
    версионируемые модели, сериализуемые в JSON; повторная доставка не меняет
    результат ([0013](decisions/0013-scaling-and-replaceable-backends.md)).
11. **Экономия ресурсов по умолчанию.** Новый браузерный слот — только при
    бюджете памяти; блокировка тяжёлых ресурсов включена; HTTP предпочтителен,
    когда сайт позволяет ([0015](decisions/0015-open-core-resources-and-runs.md)).
12. **Расширения через интерфейсы.** Транспорты, решатели капч, ИИ-наблюдатель
    подключаются к ядру через интерфейсы и не меняют исполнители страниц
    ([0012](decisions/0012-transports-isolation-and-sync-core.md), [0014](decisions/0014-captcha-solvers-and-ai-observer.md)).

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
Slot-процесс (браузерный: Chromium на пару site+proxy | HTTP-клиент; много задач)
 └── SlotRuntime
      ├── Navigator       open → bootstrap/warmup → PageStateDetector → intervention
      ├── Pipeline        DefaultPipeline | pipeline.py пакета
      │    └── PageScrapers: Auth / List / Detail (+ стратегии)
      ├── ScraperActions  hooks пакета
      └── ItemSink        partition writer + fsync → ItemsCommitted (пачками)
```

На одной машине координатор (очередь, ворота, прокси, retry) и агент хоста
(`BrowserManager` и слоты) работают в одном родительском процессе. На нескольких
машинах это разные процессы с общим хранилищем и тем же протоколом
слота ([0013](decisions/0013-scaling-and-replaceable-backends.md), раздел 6.9).

## 4. Структура пакета и слои

### 4.1 Пакеты

```
dailyscraper/
  domain/         исходы страниц и задач, категории ошибок, задачи, proxy id — чистые типы без I/O
  settings/       окружение, загрузка config/<env>/*.json, неизменяемый снимок
  schema/         модели scraper.json v3, runtime, workload; загрузка пакета парсера
  pages/          модели страниц, селекторы, XPath, компиляция templates          ← SDK
  page_scrapers/  исполнители List / Detail / Auth, registry, стратегии
  pipelines/      DefaultPipeline, PipelineContext                            ← SDK
  actions/        ScraperActions (hooks)                                      ← SDK
  execution/      SlotRuntime, Navigator, ItemSink, CompiledTemplate
  detection/      page state, ErrorClassifier                                 ← SDK (маркеры)
  transport/      интерфейсы страницы и элементов; реализации Playwright и HTTP, interventions
  browsers/       BrowserManager, Slot, точка входа слота, сессии, профили, leases, watchdog, termination
  proxies/        sources, PoolSnapshot, Allocator, HealthRegistry, network check
  tasks/          TaskQueue, planner, discovery, RetryPolicy, inputs
  dispatch/       Dispatcher, Gates, fairness
  storage/        интерфейсы хранилища; SQLite-адаптер (repositories, migrations, commands), data_path, partitions, checkpoints, артефакты
  integrations/   решатели капч (адаптеры сервисов)
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
| 3 | `execution`, `pipelines`, `page_scrapers`, `actions`, `integrations` |
| 2 | `transport`, `detection` |
| 1 | `schema`, `pages`, `settings`, `testing` |
| 0 | `domain` |

Дополнительно:

- `execution` и всё ниже не знают о `dispatch`, `proxies`, `tasks`, `storage` —
  они работают внутри слота;
- `storage` не импортирует `browsers`, `observability`, `app`.

Правило проверяет `tests/unit/test_layers.py` (AST-разбор импортов).

## 5. Доменная модель (`dailyscraper/domain`)

Типы появляются вместе с первыми потребителями: на этапе 1 — `ErrorCategory`,
`PageType`, `PageState` (с переходным `LOADING`) и `PageOutcome`; задачи,
attempts и `ProxyId` — на этапах 3–5. Селекторы — конфигурация страницы и живут
в `pages/` ([0008](decisions/0008-scraper-json-v3.md)).

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
- **Связка (сайт, прокси).** Лимит браузеров на одну прокси для одного сайта —
  правило лимита `scope: [site, proxy]` ([0016](decisions/0016-session-limits.md)). `site_key` — домен из `website` пакета, общий
  для всех парсеров этого сайта; прямое подключение без прокси — прокси `direct`.
  Lease связки общий для всех запусков и машин (одна прокси — один внешний IP).
  В режиме `persistent` лимит всегда 1: профиль `(site, ProxyId)` может открыть
  только один браузер. Итоговая параллельность сайта — меньшее из `max_parallel`
  и «доступные прокси × лимит связки».
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

- Собственные `multiprocessing.Process` (spawn). Браузерный слот держит одну
  сессию на пару `(site_key, ProxyId)`; ротация сессии по
  `max_age` / `max_jobs` / `max_items`. HTTP-слот — тот же протокол без браузера
  и с N дорожками: до N задач одновременно, у каждой своя прокси ([0013](decisions/0013-scaling-and-replaceable-backends.md)).
- Режим изоляции браузера выбирается на парсер
  ([0012](decisions/0012-transports-isolation-and-sync-core.md)): `persistent`
  (отдельный Chromium, профиль, MV3, по умолчанию), `ephemeral` (отдельный
  Chromium без профиля), `shared` (общий Chromium на хост, слоты подключаются по
  CDP и создают по контексту; после замеров). Мощность считается по виду слота.
- `kill` убивает только этот слот и его дерево (логика из MVP).
- Watchdog — по событиям `Progress` из pipe.
- Новый браузерный слот запускается только при наличии бюджета памяти машины;
  иначе задача ждёт ([0015](decisions/0015-open-core-resources-and-runs.md)).
- Лимиты — правила над измерениями `host`/`site`/`proxy`/`scraper`/`run`/`project`/`kind` ([0016](decisions/0016-session-limits.md)); места в них — leases, общие для запусков и машин, берутся на время жизни
  слота. Лимиты — настройка хоста в SQLite, а не значения из запроса.
- Слот не открывает хранилище и не читает конфиг с диска; точка входа —
  `browsers/slot_main.py`.
- Сообщения протокола — версионируемые модели, сериализуемые в JSON; у попыток,
  пачек строк и команд свои идентификаторы, повторная доставка безопасна.

| Родитель → слот | Слот → родитель |
|---|---|
| `RunTask(task_id, attempt_id, kind, payload)` | `SlotReady(pid)` / `SlotFailed(error)` |
| `Ack(ref, ok, error)` — ответ на отметку партиции | `TaskStarted(attempt_id)` |
| `Cancel()` — прервать текущую задачу | `Progress(attempt_id)` — из проверки прерывания, не чаще раза в 5 с |
| `Shutdown()` — закончить задачу, закрыть партиции, выйти | `ItemsCommitted(BatchCommit)`, `PartitionSealed(PartitionSeal)` — ждут `Ack` |
| | `TaskFinished(attempt_id, TaskOutcome)` |
| | `SessionBroken(reason)`; смерть процесса → событие `SlotExited` в родителе |

Слот ждёт `Ack` на каждую пачку строк: отказ или тишина обрезают незарегистрированные
байты и прерывают задачу, поэтому реестр в хранилище остаётся единственной правдой.
Результат задачи каталога берётся из последней попытки, сохранившей строки; детали —
по последнему статусу каждой строки входа.

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

`queue.claim` учитывает `max_parallel` парсера (и сайта, если задан) вместе с
лимитами хоста: «15 одновременных задач для сайта A» — настройка парсера, а не
число процессов.

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

### 6.8 Хранилище и бэкенды (`storage/`)

| Интерфейс | Первая реализация | Для нескольких машин |
|---|---|---|
| Очередь задач и попыток | SQLite | PostgreSQL (`FOR UPDATE SKIP LOCKED`) |
| Run, команды, события | SQLite | PostgreSQL; команды — с уведомлением через Redis |
| Leases, лимиты хоста | SQLite | PostgreSQL или Redis с TTL |
| Health прокси | SQLite + TTL | Redis с TTL |
| Артефакты: partitions, снимки ошибок, экспорт | локальная ФС | S3-совместимое хранилище |

- Модули выше `storage/` не импортируют `sqlite3` и не пишут SQL.
- Контрактные тесты: один набор тестов поведения на каждый интерфейс, который
  запускается против каждой реализации.
- Время leases и `not_before` — часы хранилища; идентификаторы строковые.
- Run, задачи, события и артефакты несут `project_id` (по умолчанию
  `default`) — фундамент для нескольких клиентов без логики клиентов в ядре
  ([0015](decisions/0015-open-core-resources-and-runs.md)).

### 6.9 Несколько машин

- **Координатор**: очередь, ворота, выбор прокси, retry, финализация run.
  **Агент хоста**: `BrowserManager`, слоты, лимиты своей машины, heartbeat.
- Агенты получают задачи и сообщают события через те же модели протокола, что
  слоты — родителю. Lease машины истекает без heartbeat, её задачи возвращаются
  в очередь.
- Attempts, leases, слоты и события несут `host_id`.
- Реализация — этап 10; до него соблюдаются только правила фундамента:
  интерфейсы хранилища, сериализуемый протокол, идемпотентность, `host_id`.

### 6.10 Интеграции ([0014](decisions/0014-captcha-solvers-and-ai-observer.md))

- **Решение капч.** `CaptchaSolver` вызывается из точки вмешательства
  `StateWaiter`; цепочка «решатель → ручное решение → отказ» настраивается на
  парсер; расходы и исходы — в событиях, лимиты — на run и парсер.
- **Ручное решение и уведомления — раньше автоматики.** Слот держит страницу,
  отправляет уведомление (почта, Telegram) и ждёт решения человеком в пределах
  таймаута; Dispatcher и watchdog видят состояние «ждёт вмешательства».
  Решатели через сервисы и ИИ-модели встают в ту же цепочку позже.
- **ИИ-наблюдатель** — отдельный компонент вне ядра. Получает события, снимки
  страниц при ошибках и версии пакетов; изменения `scraper.json` создаёт как
  предложения, которые проходят проверку схемы, повтор извлечения на снимках и
  контрольный прогон. Применение — человеком или автоматически по политике
  парсера, с новой версией пакета и откатом.

### 6.11 Запуски, история и доставка ([0015](decisions/0015-open-core-resources-and-runs.md))

- **История.** Запуск хранится со статусом, временем, снимком настроек,
  версиями пакетов, итогами и файлами; CLI показывает список и открывает
  результаты любого запуска. Срок хранения — retention.
- **Ключ товара.** Строка результата получает `item_key` из `identity_fields`
  пакета или ссылки на detail; по нему позже строятся история и сравнение
  запусков.
- **Версия пакета** — хэш содержимого `scraper.json` и `.py`.
- **Доставка.** После финализации — список доставок из `output.json`: форматы
  (CSV, JSON, XLSX, XML), архив, получатель (локальная папка, веб-хранилище,
  почта, мессенджер), условие (успех, любой исход). Получатель — адаптер; сбой
  доставки повторяется и пишется в события, но не меняет результат запуска.

## 7. Жизненный цикл run

1. `RunService` делает снимок конфига и резервирует `run_id`.
2. Startup reconciliation осиротевших run.
3. `PoolSnapshot`: источники → дедупликация → network check → начальный health.
4. Planner: каталог → `DISCOVER`/`LIST`; input → `DETAIL` по диапазонам
   immutable snapshot; `PROBE` для ворот.
5. `Dispatcher` работает до исчерпания очереди или terminal-команды.
6. Финализация **при любом исходе**: merge/export, summaries, terminal-статус
   run и команд; затем доставки, уведомления и retention.

Resume/retry/rebuild — новый run из задач и attempts исходного.

## 8. Конфигурация

`config/<env>/*.json`, окружение выбирает `DAILYSCRAPER_ENV`
(`development` по умолчанию, `testing` для тестов). Порядок источников,
`*.local.json`, переопределения `DAILYSCRAPER__…` и `${env:NAME}` —
[0010](decisions/0010-settings-loading.md). Секции появляются вместе с их
потребителями; сейчас есть `paths` и `scrapers`.

| Файл | Содержание |
|---|---|
| `paths.json` | Каталоги данных, логов, профилей, runtime |
| `logger.json` | Логирование |
| `browser.json` | Playwright, профили, proxy-auth, stealth, блокировка ресурсов |
| `runtime.json` | Слоты и тайм-ауты, retry, watchdog, правила лимитов `limits` ([0016](decisions/0016-session-limits.md)) |
| `proxies.json` | Схема v2 MVP (static/gateway/api) + `id_strategy`, `gateway.session_rotation` |
| `proxy_check.json` | Network check и пороги ворот |
| `scrapers.json` | Выбранные парсеры, aliases, подмножество прокси |
| `workload.json` | Segments: link group × диапазоны list/detail; `proxy_id` — только affinity |
| `output.json` | Доставки: форматы, архив, получатель, условие |
| `storage.json` | Retention |

`scraper.json` v3 — [0008](decisions/0008-scraper-json-v3.md), пакет парсера —
[0003](decisions/0003-separate-scrapers-repository.md), hooks —
[0009](decisions/0009-scraper-actions-hooks.md). Примеры —
`tests/scrapers/controlled_catalog/` и `tests/scrapers/controlled_details/`.

```json
{
  "schema_version": 3,
  "name": "example_catalog",
  "website": "https://example.com",
  "links": [{"id": "main", "type": "list", "urls": ["https://example.com/catalog/"]}],
  "navigation": {"warmup_urls": [], "open_target_after_warmup": true},
  "health_check": {"urls": ["…"], "min_items": 1, "required_fields": []},
  "discovery": {"enabled": true, "empty_confirmations": 2},
  "inputs": {"detail_link_field": "detail_link", "identity_fields": []},
  "page_state": {"wait_attempts": 3},
  "pages": {
    "list": {
      "templates": [{
        "name": "grid",
        "first_item": {"body": {"css": "li.product"}, "fields": {"name": {"css": ".title"}}},
        "next_page": {"css": "a.next"}
      }],
      "pagination": {"start_page": 1, "last_page": 0, "url_template": "{url}page/{page}/"}
    },
    "detail": {
      "templates": [{"name": "detail", "fields": {"title": {"selectors": [{"xpath": "//h1"}, {"css": "h1"}]}}}]
    }
  },
  "options": {}
}
```

## 9. CLI

Одна точка входа `dailyscraper` (`[project.scripts]`; `run` и `runs` — с этапа 3) с подкомандами `run`,
`status`, `runs` (история и результаты запусков), `stop`, `resume`, `retry`,
`rebuild`, `storage`. CLI только вызывает сервисы `app/` — это условие для
HTTP API и веб-интерфейса, которые подключаются отдельно ([0015](decisions/0015-open-core-resources-and-runs.md)).

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
| Лимит одновременности парсера | число процессов | `max_parallel` парсера |
| Хранилище | SQLite и файлы, SQL по всему коду | интерфейсы; SQLite и ФС — первые реализации, PostgreSQL / Redis / S3 — адаптеры |
| Машин | одна | одна; фундамент готов к нескольким |
| Запуск браузера при нехватке памяти | да | нет: слот ждёт бюджета памяти |

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
| 3 | **Хранилище и выход.** Интерфейсы хранилища и SQLite-адаптер (run и история запусков, команды, события с версией схемы, `project_id`), хранилище артефактов на локальной ФС, data_path, partitions + checkpoints, `item_key`, версия пакета (хэш) в попытке, merge/export, доставка в локальную папку и архив, финализация при любом исходе | Контракты output и финализации из MVP проходят; контрактные тесты бэкендов проходят на SQLite |
| 4 | **Параллельность.** TaskQueue и таблицы `tasks`/`attempts`, BrowserManager (слоты, pipe, watchdog, kill одного слота, leases хоста), Dispatcher, stop/cancel/kill; протокол слота — версионируемые JSON-модели, пакетные отметки, идемпотентность, `host_id`; `max_parallel` парсера; снимки страниц при ошибках | Перенесены и проходят acceptance: browser close, worker crash, interrupt, parent loss, multi run; убийство слота не трогает соседей |
| 4a | **HTTP-транспорт.** Интерфейсы страницы, поиска элементов и пробы; Playwright — одна реализация, HTTP — вторая; HTML-режим на селекторах `scraper.json` и API-режим — код пакета поверх HTTP-клиента фреймворка (GET/POST JSON и форм, тайм-ауты и повторы запроса, cookies, встроенный JSON страницы: сбалансированные скобки, `__NUXT_DATA__`, `__NEXT_DATA__`, JSON-LD; несколько строк на товар); категории × диапазоны страниц → задачи вместо ручной раскладки по потокам; HTTP-слот с дорожками; `"transport"` в `scraper.json`, отказ при загрузке для возможностей, которых у HTTP нет ([0012](decisions/0012-transports-isolation-and-sync-core.md), [0013](decisions/0013-scaling-and-replaceable-backends.md)) | Контролируемые пакеты проходят и через браузер, и через HTTP с одинаковым результатом; `transport/` — единственное место, где импортируется Playwright; нагрузочный тест на 100 дорожек проходит, замер на 1000 записан |
| 4b | **Ручное решение капч и уведомления.** Ручное вмешательство из MVP в точке `StateWaiter`, состояние слота «ждёт вмешательства», уведомления через адаптеры (почта, Telegram) о капче и об итогах запуска ([0014](decisions/0014-captcha-solvers-and-ai-observer.md)) | На controlled site капча ждёт ручного решения, уведомление уходит в тестовый получатель, после решения задача продолжается; по таймауту — категория `captcha` |
| 5 | **Прокси.** Sources, PoolSnapshot, `ProxyId`, Allocator, health на хост, профили, MV3, резидентские сессии; выбор прокси с учётом лимитов связки (сайт, прокси), правило профиля для `persistent`; режимы изоляции `persistent` и `ephemeral` (проверка встроенной авторизации прокси); блокировка ресурсов, бюджет памяти для слотов | Тесты прокси и health store проходят; соседний run видит открытый circuit; acceptance с CONNECT-прокси (407 → другая прокси) |
| 6 | **Ворота, discovery, probe.** `PROBE`/`DISCOVER`, ворота на парсер, пороги run | Парсер A работает, пока B на probe; planning stop и discovery recovery проходят |
| 6a | **Автоматическое решение капч.** `CaptchaSolver`, адаптер сервиса, решатель на ИИ-модели, цепочка на парсер перед ручным решением, учёт расходов ([0014](decisions/0014-captcha-solvers-and-ai-observer.md)) | Капча на controlled site решается через тестовый решатель; отказ решателя ведёт к ручному решению или категории `captcha` |
| 7 | **Боевые парсеры.** Репозиторий парсеров; живые пакеты: шесть браузерных (каталог, каталог с деталями, input-режим) и около двенадцати HTTP-сайтов (JSON API и HTML), которые сейчас работают на PHP; парный прогон со старыми парсерами | Совпадение row_id/полей с MVP ≥ согласованного порога; многочасовой прогон без вмешательства |
| 8 | **Паритет эксплуатации.** CLI целиком (с историей запусков), async-фасад сервисов `app/`, получатели доставки (веб-хранилище, почта, мессенджеры), остальные уведомления, консоль, retention, Docker-образ и шаблон развёртывания, документация ([0015](decisions/0015-open-core-resources-and-runs.md)) | MVP можно отключить; 10–15 сайтов работают сутки в контейнере с 8 ГБ памяти и 2 ядрами |
| 9 | **ИИ-наблюдатель.** Отдельный компонент: события и снимки → предложения изменений `scraper.json` → проверка (схема, повтор на снимках, контрольный прогон) → применение по политике с версией и откатом ([0014](decisions/0014-captcha-solvers-and-ai-observer.md)) | Намеренно сломанный селектор на controlled site обнаружен, исправление предложено, проверено и применено |
| 10 | **Несколько машин.** Адаптеры PostgreSQL, Redis, S3; агент хоста; координатор отдельно от агентов ([0013](decisions/0013-scaling-and-replaceable-backends.md)) | Контрактные тесты бэкендов проходят на PostgreSQL и Redis; run на двух машинах; отключение машины возвращает её задачи в очередь |

Этапы 2 и 3 можно вести параллельно; этапы 4a, 4b и 5 зависят от 4. Режим изоляции
`shared` вводится после замеров памяти, не раньше этапа 5. Порядок этапов 9 и 10
определяется потребностью; оба зависят от 8.

Ближайшая цель — работающий сервис для одного владельца на одной машине: этапы
3–7. HTTP API и веб-интерфейс — отдельный пакет после этапа 8.

Порядок работ после этапа 4: **5 → 4b → 6 → 6a → 7 (браузерные пакеты) → 4a →
7 (HTTP-пакеты) → 8**. Этап 5 выполнен. Браузерные пакеты переносятся первыми; HTTP-транспорт и
перенос HTTP-сайтов — в конце, перед паритетом эксплуатации.

## 13. Принятые решения

| Вопрос | Решение |
|---|---|
| Sync или async | Синхронный Dispatcher и код страниц, одновременность — процессами; async-фасад для встраивания — [0002](decisions/0002-sync-dispatcher-and-process-slots.md), [0012](decisions/0012-transports-isolation-and-sync-core.md) |
| HTTP-транспорт | Этап 4a, за общим интерфейсом транспорта — [0012](decisions/0012-transports-isolation-and-sync-core.md) |
| Браузеры или контексты | Отдельный Chromium на `(site, proxy)` по умолчанию; режимы `ephemeral` и `shared` — [0012](decisions/0012-transports-isolation-and-sync-core.md) |
| FastAPI | Отложен до этапа 8+ — [0002](decisions/0002-sync-dispatcher-and-process-slots.md) |
| Нагрузка и несколько машин | Дорожки HTTP-слота, `max_parallel`, пакетные отметки; интерфейсы хранилища, PostgreSQL / Redis / S3 — адаптерами; координатор и агенты хостов — [0013](decisions/0013-scaling-and-replaceable-backends.md) |
| Границы ядра | Открытая библиотека и CLI; HTTP API, веб-интерфейс, облачные надстройки — поверх `app/`, вне ядра; `project_id` как фундамент для клиентов — [0015](decisions/0015-open-core-resources-and-runs.md) |
| Ресурсы | 10–15 сайтов на 8–16 ГБ и 2–4 ядрах; бюджет памяти для слотов, блокировка ресурсов по умолчанию — [0015](decisions/0015-open-core-resources-and-runs.md) |
| Запуски и доставка | История запусков, `item_key`, версия пакета — хэш; доставки по настройкам через адаптеры получателей — [0015](decisions/0015-open-core-resources-and-runs.md) |
| Прокси | Пул со стабильными `ProxyId`, здоровье `(site, proxy)` в общем хранилище, ротация сессий gateway по `generation`, `ProxyAllocator`, network check, режимы `ephemeral`/`persistent` — [0017](decisions/0017-proxies.md) |
| Лимиты сессий | Правила над измерениями сессии, переопределения по имени, leases в общем хранилище — [0016](decisions/0016-session-limits.md) |
| Капчи и ИИ-наблюдатель | `CaptchaSolver` в точке вмешательства; наблюдатель вне ядра, изменения — через проверяемые предложения — [0014](decisions/0014-captcha-solvers-and-ai-observer.md) |
| Репозиторий и имена | Новый `dailyscraper`, чистые имена — [0001](decisions/0001-clean-repository-and-naming.md) |
| Пакеты парсеров | Отдельный новый репозиторий, формат v3, одноразовый конвертер — [0003](decisions/0003-separate-scrapers-repository.md) |
| Ветки | `dev` → `master` по завершении этапа — [0004](decisions/0004-branches-and-git-ownership.md) |
| Инструменты | uv, ruff, Python 3.13 — [0005](decisions/0005-uv-ruff-python-313.md) |
| P0-баги MVP | Исправлены в MVP (407, финализация run, падение парсера, Noop store, закрытие SQLite); в новом коде закрываются конструкцией |
