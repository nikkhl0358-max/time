# Timeweb Cloud: PostgreSQL для Timetable v1717

## Переменная приложения
В App Platform добавьте секрет:

`DATABASE_URL=postgresql://gen_user:ПАРОЛЬ@ХОСТ:5432/default_db?sslmode=require`

Пароль храните только в секретах Timeweb. Если используете `sslmode=verify-full`,
нужно также установить корневой сертификат Timeweb в контейнер и указать его через
стандартную переменную `PGSSLROOTCERT`.

Если `DATABASE_URL` задан, PostgreSQL становится единственным рабочим источником.
SQLite/JSON используются только когда `DATABASE_URL` отсутствует.

## Миграция существующего raspisanie.db

На машине, где лежит файл базы:

```bash
pip install -r requirements.txt
export DATABASE_URL='postgresql://gen_user:ПАРОЛЬ@ХОСТ:5432/default_db?sslmode=require'
python migrate_sqlite_to_postgres.py /путь/к/raspisanie.db
```

Мигратор:
- открывает SQLite только для чтения;
- отказывается перезаписывать непустую PostgreSQL-базу;
- переносит `kv_store`;
- переносит историю публикаций из `publication_snapshots`;
- создаёт первичный резервный снимок в PostgreSQL.

После миграции проверьте `/healthz`: ожидается `storage=postgresql` и `storageReady=true`.

## Авторасписание
Автогенератор не зависит от типа хранилища: `server_scheduler.mjs` сохранён полностью.
Обычный, быстрый, групповой и недельный расчёт продолжают работать через те же API.

## Жёсткое правило формата дня
ДО и очные занятия нельзя смешивать в один день для одной группы или преподавателя,
если их недели реально пересекаются. Это hard constraint и в автоматическом, и в
ручном режиме. Непересекающиеся недели не конфликтуют.
