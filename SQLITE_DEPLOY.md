# Подключение SQLite-хранилища

## Amvera
В проекте уже задано:
- `DATA_DIR=/data`
- `SQLITE_PATH=/data/raspisanie.db`
- `persistenceMount: /data`

После деплоя дополнительная ручная инициализация базы не требуется. Если на постоянном диске лежит старый `/data/storage.json`, приложение импортирует его в SQLite при первом обращении к данным.

## Timeweb Cloud App Platform
Подключите постоянный диск/volume к пути `/data` и задайте переменную:

`SQLITE_PATH=/data/raspisanie.db`

Не храните БД внутри `/app`: этот каталог может пересоздаваться при деплое.

## Проверка
Откройте `/healthz`. Ожидаемый ответ содержит:
- `"storage": "sqlite"`
- `"sqlitePath": "/data/raspisanie.db"`
- `"storageReady": true`

## v1712: что хранится где

- `/data/raspisanie.db` — канонические рабочие данные и исторические снимки публикаций.
- `/data/backups/storage_*.json.gz` — переносимые резервные копии; старые `.json` также поддерживаются.
- `/data/public-*.json*`, `/data/public-shards/` — производный публичный snapshot; его можно пересобрать из SQLite.
- `/data/storage.json` — только старый страховочный файл миграции, после перехода не обновляется.
- `/tmp/timetable-generation-progress-*` — временный прогресс расчёта; не является пользовательскими данными.

После деплоя проверьте `/healthz`: `sqliteOk=true`, `dataDirWritable=true`, `storageReady=true`.
