# Заметки

## Окружение разработки на Windows

- **`SSLKEYLOGFILE` от антивируса.** Некоторые антивирусы с HTTPS-сканированием
  выставляют `SSLKEYLOGFILE` в путь устройства. Сборки Python 3.13, которые
  ставит uv на Windows, на первом TLS-соединении тогда падают с
  `OPENSSL_Uplink … no OPENSSL_Applink`, без traceback. В тестах переменная
  убирается в `tests/bootstrap.py`. Для runtime решить на этапе 5, когда
  появятся HTTPS-запросы из Python (network check прокси).
- **Предупреждение uv о hardlink.** Если кэш uv и проект на разных дисках,
  hardlink невозможен. На работу не влияет; предупреждение убирается переменной
  `UV_LINK_MODE=copy`.

## Controlled site

- «Controlled site» — детерминированный сайт `https://dailyscraper.com/tests/`,
  а не локальный сервер. Acceptance-тесты требуют сети.
- Контракт данных: 3 страницы каталога (9/9/1), 19 товаров, стабильные товары
  `hd1` и `t-short-9` — `tests/fixtures/expected/controlled_dataset_v1.json`.
- Названия товаров на сайте содержат старое название проекта. Это данные сайта,
  а не код; менять их — отдельная задача на сайте с обновлением fixtures.
