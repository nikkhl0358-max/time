# UML проекта timetable22

Обновлено: 2026-11-06 — разделение 50+ МБ проекта на легкий bootstrap, частичную загрузку разделов, потоковый экспорт, изоляцию планировщиков и строгий аудит бизнес-логики.

## Компоненты

```mermaid
flowchart LR
    subgraph Frontend["Vue 3 Frontend (3D Spatial Edition)"]
        App[frontend/src/App.vue]
        Navbar[Navbar.vue · Responsive Nav & Roles]
        Hero[HeroScene3D.vue · Three.js Central Orange Orb]
        DrillDown[GroupDrillDown.vue · Specialty->Year->Group Cards]
        EntityPicker[TeacherRoomPicker.vue · Teachers Alphabet & Campus Rooms]
        Viewer[ScheduleViewer.vue · Spatial Grid]
        Card[LessonCard.vue · Lesson Card]
        SpatialCard[SpatialCard.vue · 3D Specular Tilt]
        Filter[FilterBar.vue · Breadcrumb & Week Navigation]
        Modal[Modal.vue · Accessible Modal & Esc]
        Bells[BellsSchedule.vue · Live Timer]
        Subs[SubstitutionsFeed.vue · Substitutions]
        AuthModal[AdminAuthModal.vue · Safe Login]
        Admin[AdminLayout.vue · Manager Dashboard]
        AdminSubs[AdminSubstitutionsEditor.vue · Reactive CRUD]
        
        App --> Navbar
        App --> Hero
        App --> DrillDown
        App --> EntityPicker
        App --> Viewer
        Viewer --> Card
        Card --> SpatialCard
        App --> Filter
        App --> Modal
        Modal --> Bells
        Modal --> Subs
        App --> AuthModal
        AuthModal --> Modal
        App --> Admin
        Admin --> AdminSubs

        App --> SchedComposable[composables/useScheduleData.js]
        App --> AuthComposable[composables/useAuth.js]
        App --> TimeComposable[composables/useCurrentTime.js]
        App --> ExportComposable[composables/useScheduleExport.js]
        SpatialCard --> TiltComposable[composables/use3DTilt.js]
    end

    SchedComposable --> StorageClient[frontend/src/storage.js]
    AuthComposable --> AuthClient[frontend/src/auth.js]
    Admin --> EditorClient[frontend/src/editorTransport.js]

    AuthClient --> Auth[auth.py]
    StorageClient --> API[app.py]
    EditorClient --> API
    API --> EditorTransport[editor_transport.py]
    API --> EditorState[editor_state.py]
    Legacy[tools/app.py] --> API
    API --> Auth
    Auth --> Key[DATA_DIR session signing key]
    Auth --> SessionRevoke[Server session revocation table]
    API --> SQLite[(SQLite)]
    API --> PgCompat[pg_compat.py]
    PgCompat --> Postgres[(PostgreSQL)]
    API --> Backups[DATA_DIR backups]
    API --> Public[Public snapshot shards]
    API --> GenWorker[SchedulerGenerate worker]
    API --> OptWorker[SchedulerOptions worker]
    GenWorker --> Scheduler[server_scheduler.mjs]
    OptWorker --> Scheduler
    API --> GraphScheduler[server_graph_scheduler.mjs]
```

## Хранение

```mermaid
erDiagram
    kv_store {
        text key PK
        text value_json
        real updated_at
    }
    publication_snapshots {
        text store_key
        text version_id
        text snapshot_json
    }
```

Рабочий проект и пользователи находятся внутри JSON `schedule-data-v2`.
Точные дополнительные поля и индексы определяются инициализацией в `app.py`.
SQLite и PostgreSQL используют совместимый слой `pg_compat.py`.

## API и контроль доступа

```mermaid
flowchart TD
    Request[API request] --> Headers[after_request security headers: X-Frame, nosniff, Referrer]
    Request --> Guard[guard_api]
    Guard --> PublicGet[Allowlisted public GET and HEAD: index, shards, status, bootstrap, availability]
    PublicGet --> PublicSource[Public draft isolation: publishedSnapshot only]
    Guard --> AuthRoutes[auth session login logout setup]
    Guard --> Session[current_user: check session_id and is_session_revoked]
    Session --> Private[Storage presence generators diagnostics]
    Session --> EditorTransportRoutes[Editor bootstrap sections schedule export: sanitize_users]
    Session --> Superadmin[Superadmin RBAC: backup restore download create and user edits]
    Private --> PartialCheck[partial_write_error and complete_project]
    PartialCheck --> PreserveCheck[preserve_deferred_sections and merge_users_preserve_hashes]
    PreserveCheck --> Writes[Atomic storage writes]
    Superadmin --> Writes
```

| Маршрут | Назначение |
| --- | --- |
| GET /api/auth/session | Состояние входа без списка пользователей |
| POST /api/auth/login | Серверная проверка пароля |
| POST /api/auth/logout | Удаление cookie входа |
| POST /api/auth/setup | Первый администратор по AUTH_SETUP_TOKEN |
| POST /api/auth/password | Смена собственного пароля |
| GET /api/public-* | Опубликованные данные по явному списку |
| GET /api/editor/\<key\>?section=bootstrap | Быстрый bootstrap (~71 КБ gzip) без тяжёлых разделов |
| GET /api/editor/\<key\>?section=\<s\>&token=\<t\> | Посекционная загрузка (loads, schedule, graphState и др.) |
| GET /api/editor/\<key\>/schedule?groupId=\<id\>&week=\<w\> | Рабочие occurrences конкретной группы/недели |
| GET /api/editor/\<key\>/export | Потоковая выгрузка полного переносимого проекта |
| /api/storage* | Закрытый редакторский контур (защита от неполной перезаписи) |
| /api/schedule/*, /api/graphs/* | Закрытые серверные планировщики |

Сессия подписана постоянным ключом. Пароль и роль сверяются с хранилищем на
каждом закрытом запросе. Клиентский ID пользователя не даёт полномочий.

## Классы

```mermaid
classDiagram
    class _PersistentSchedulerWorker {
        +request(payload, cache_key, data, timeout)
    }
    class PgConnection {
        +execute(sql, params)
        +commit()
        +rollback()
        +close()
    }
```

## Производительность и шардирование хранилища

### Точки выдачи и оптимизация Scope Matching

```mermaid
sequenceDiagram
    autonumber
    actor User as Браузер / Бот
    participant API as app.py
    participant FastMatch as _instance_has_group
    participant Scope as _editor_matches_scope
    participant Transport as editor_transport.py
    participant DB as SQLite kv_store

    User->>API: GET /api/editor/schedule-data-v2/schedule?groupId=9b28grdj
    activate API
    API->>Transport: schedule_response(groupId=9b28grdj)
    activate Transport
    Transport->>DB: Чтение снимка проекта
    DB-->>Transport: Полный проект (22882 пар)
    loop Для каждой из 22882 пар
        Transport->>FastMatch: _instance_has_group(inst, groupId)
        alt Группа не совпадает (98.7% пар)
            FastMatch-->>Transport: False (ранний выход без вычисления 20 недель)
        else Группа совпадает (295 пар)
            Transport->>Scope: Полная проверка недель нагрузки и потоков
            Scope-->>Transport: True/False
        end
    end
    Transport-->>API: Срез группы (314 KB raw / 15.9 KB gzip, ~136 ms)
    deactivate Transport
    API-->>User: HTTP 200 OK
    deactivate API
```

| Эндпоинт | Raw Size | Gzip Size | Экономия сжатия | Latency (avg) |
| --- | --- | --- | --- | --- |
| `/api/storage/schedule-data-v2?full=1` | 56160.2 KB | 2916.5 KB | 94.8% | 349.6 ms |
| `/api/editor/schedule-data-v2?section=bootstrap` | 659.8 KB | 68.6 KB | 89.6% | 19.0 ms |
| `/api/editor/schedule-data-v2/schedule?groupId=9b28grdj` | 314.2 KB | 15.9 KB | 94.9% | 136.6 ms |
| `/api/editor/schedule-data-v2/schedule?groupId=9b28grdj&week=1` | 19.7 KB | 2.2 KB | 88.6% | 44.9 ms |
| `/api/editor/schedule-data-v2?section=loads` | 1651.0 KB | 87.3 KB | 94.7% | 24.9 ms |
| `/api/editor/schedule-data-v2?section=graphState` | 1003.2 KB | 61.1 KB | 93.9% | 21.0 ms |
| `/api/public-index` | 201.4 KB | 21.9 KB | 89.1% | 1.5 ms |
| `/api/public-schedule/group/9b28grdj` | 361.5 KB | 19.8 KB | 94.5% | 5.0 ms |

### Гранулярная гидратация вкладок и оптимизация рендеринга (Client-Side Performance)

```mermaid
flowchart TD
    subgraph UI["Административная панель (App.jsx)"]
        Tabs["21 Вкладка редактора"] --> LP["LoadsPanel (Нагрузки)"]
        Tabs --> GP["GraphsPanel (Графики)"]
        Tabs --> CC["ConflictCenterPanel (Конфликты)"]
        Tabs --> LightTabs["18 Остальных вкладок"]
    end

    subgraph LP_Opt["Оптимизация LoadsPanel"]
        LP --> PageCtrl["LoadsPagination (100 строк на страницу)"]
        PageCtrl --> DOM["DOM: ~1,500 узлов вместо ~60,000 (109 мс вместо 13,194 мс)"]
    end

    subgraph GP_Opt["Оптимизация GraphsPanel"]
        GP --> MapIdx["Индексы sameGroupSubjectTypePeers + graphLoadsByGroup"]
        MapIdx --> O1Lookup["O(1) доступ к строкам потоков (108 мс вместо 9,867 мс)"]
        GP --> ScarcityCache["Кэш дефицитности: graphScarcityProfileCache"]
    end

    subgraph CC_Opt["Оптимизация ConflictCenterPanel"]
        CC --> AsyncStep["Отложенный расчет через setTimeout (0 мс блокировки)"]
        CC --> ResourceBucket["Slot-бакетирование по преподавателям/аудиториям/группам"]
        ResourceBucket --> CCFast["118 мс вместо 6,611 мс"]
    end
```

#### Замеры скорости переключения вкладок (E2E Benchmark, 21 вкладка, проект 57.5 МБ)

| Вкладка | Время до оптимизации | Время после оптимизации | Ускорение |
| --- | --- | --- | --- |
| **Нагрузки** (3,995 строк) | **13 194 мс** | **109 мс** | **121x** |
| **Графики** (расчёт 20 недель) | **9 867 мс** | **108 мс** | **91x** |
| **Центр конфликтов** (O(N²) -> O(N)) | **6 611 мс** | **118 мс** | **56x** |
| Расписание | 2 051 мс | 108 мс | 19x |
| Фактические часы | ~1 800 мс | 298 мс | 6x |
| Списки / Учебный план / Замены / etc. | 500–800 мс | 105–236 мс | 3–5x |

14 из 21 вкладок панели администратора (`lists`, `constraints`, `announcements`, `consultations`, `curriculum`, `substitutions`, `scenarios`, `attestation`, `practices`, `history`, `versions`, `config`, `users`, `semester`) являются легковесными и отображаются мгновенно (0 мс) из уже полученного `bootstrap` без фоновых запросов к сети. Тяжёлые разделы (`loads`, `graphState`, `schedule`) запрашиваются инкрементально только при переходе на соответствующие специализированные вкладки, исключая повторную загрузку уже имеющихся в памяти данных.

### Гибридная Dual-Engine архитектура: Публичный Vue 3D портал + Административная React-панель (Вариант 2)

```mermaid
flowchart TD
    Client["Клиент (Браузер / Telegram WebApp)"] --> Router{"main.js: Проверка маршрута"}
    
    subgraph Public["Публичный портал (Vue 3 + Three.js 3D)"]
        Router -->|"/", "/group/*", etc.| MountVue["mountVue.js (170 kB gzip)"]
        MountVue --> Hero3D["HeroScene3D.vue (Three.js кинетическая архитектурная призма)"]
        MountVue --> Spatial["SpatialCard.vue + use3DTilt.js (CSS 3D perspective)"]
        MountVue --> SchedView["ScheduleViewer.vue (6-дневная сетка пар)"]
        MountVue --> SubView["SubstitutionsFeed.vue (Оперативные замены)"]
        MountVue --> BellView["BellsSchedule.vue (Звонки 1-7)"]
        MountVue --> ExportCSV["useScheduleExport.js (CSV + защита от Formula Injection)"]
        MountVue --> Footer["AppFooter.vue (Подвал + Вход для составителя)"]
    end

    subgraph Admin["Панель администратора (Full React Admin Suite + 3D Theme)"]
        Router -->|"/admin", "/admin/*", "#admin"| MountAdmin["mountAdmin.jsx (Динамический импорт 674 kB gzip)"]
        MountAdmin --> AdminTheme["admin-theme.css (3D стиль: Белый, Лазурно-голубой, Оранжевый)"]
        MountAdmin --> AppJSX["App.jsx (32,000 строк, 21 вкладка)"]
        AppJSX --> Scheduler["AutoScheduler (Simulated Annealing + Constraints)"]
        AppJSX --> Publication["publishSchedule -> Генерация публичных шардов"]
    end

    subgraph Backend["Flask Бэкенд + SQLite База Данных"]
        MountVue -->|GET /api/public-index| APIIndex["app.py: /api/public-index"]
        MountVue -->|GET /api/public-schedule| APIShard["app.py: /api/public-schedule/{kind}/{id}"]
        AppJSX -->|POST /api/storage/schedule-data-v2| APISave["app.py: save_store()"]
        APISave --> DB[(data/raspisanie.db)]
        DB --> Shards[(data/public_shards)]
    end

    subgraph TelegramBot["Telegram Бот para"]
        BotWorker["services/schedule_parser.py"] -->|GET /api/public-index| APIIndex
        BotWorker -->|GET /api/public-schedule/group/{id}| APIShard
        BotWorker --> Cache[(Redis Cache & Fallback)]
    end
```

#### Гарантии безопасности и интеграции
1. **Защита от утечки учетных записей:** В эндпоинте `/api/storage/schedule-data-v2?full=1` хэши паролей `passwordHash` автоматически отсекаются с помощью `sanitize_users()` перед отправкой клиенту.
2. **Защита от Formula Injection (CWE-1236):** При экспорте расписания в CSV любые формульные префиксы (`=`, `+`, `-`, `@`, `\t`, `\r`) экранируются апострофом `'`.
3. **Безопасность WebGL-контекста:** В компоненте `HeroScene3D.vue` реализована полная очистка памяти (`disposeThreeObject`) с рекурсивным освобождением геометрий, материалов, текстур и вызовом `renderer.forceContextLoss()`.
4. **Сохранение контракта Telegram-бота `para`:** Публичные шарды полностью совместимы с парсерами бота `services/schedule_parser.py`. Все 119 интеграционных тестов расписания в репозитории `para` проходят со 100% успехом.
5. **Интеграция резервной копии данных:** Задействован полный дамп `data/backups/storage_20261106T033021_614466Z.json.gz` (85 групп, 146 преподавателей, 22 882 инстанса расписания, 452 оперативные замены, недели 8-10).
6. **Мобильная адаптивность и оптимизация производительности:** Кнопка «Войти (Админ)» перемещена в эргономичный подвал (`AppFooter.vue`), как в оригинале. На мобильных устройствах все элементы управления (роли, поиск, недели, экспорт) оптимизированы под touch-взаимодействие (высота кнопок $\ge$ 44px, `touch-action: manipulation`, отсутствие задержки нажатия). В Three.js внедрен `IntersectionObserver` и отслеживание `visibilitychange` для остановки анимационного цикла при скрытии сцены.
7. **Симметричная центрированная сетка расписания:** Устранено асимметричное смещение колонок (когда Пятница и Суббота прижимались к левому краю, оставляя пустую правую половину экрана). Внедрена сбалансированная сетка $3 \times 2$ (по 3 дня в ряду на десктопе, 2 на планшетах, 1 на смартфонах) с центрированием `justify-content: center; margin: 0 auto`. Добавлена центрированная панель быстрого выбора дня недели («Вся неделя» / «Пн» / «Вт» / ... / «Сб»).
8. **Информативность карточек пар и поддержка мероприятий:**
   - **Расписание звонков и хронология:** В бейдже слота пары отображается номер и интервал времени звонков (`Пара 1 · 8:30–10:00`). Полностью поддерживаются мероприятия (`isScheduleEvent`, `eventStartTime`–`eventEndTime`), которые могут проходить между часами пар: алгоритм сортировки рассчитывает время начала в минутах и корректно встраивает карточку мероприятия в хронологическую последовательность дня.
   - **Маркировка аудиторий:** Номера аудиторий снабжены понятной для студентов и родителей подписью `Ауд. <номер>` (`🚪 Ауд. 37`, `🚪 Ауд. 6`), текстовые названия залов сохраняются (`🚪 Спортивный зал (1)`).
   - **Тип и формат занятий:** Восстановлены бейджи типов занятий с полными наименованиями (`Лекция`, `Практика`, `Лабораторная`, `Зачёт`, `Диф. зачёт`) и индивидуальными цветовыми маркерами, формат проведения занятия (`🌐 Дистант` / `🏫 Очно`), а также полные подписи подгрупп (`1 подгруппа`, `2 подгруппа`).
9. **Синхронизация URL, навигация и персистентность выбора (F5 & Deep Links):**
   - **Защита от сброса при обновлении страницы:** Выбранная сущность (группа, преподаватель или аудитория) и номер недели сохраняются в `localStorage` (`timetable_public_selection`) и синхронизируются с адресной строкой через `useRouteSync.js` (`/group/<id>?week=<w>`, `/teacher/<id>`, `/room/<id>`). При перезагрузке страницы (F5) или переходе по прямой ссылке расписание восстанавливает выбранную группу без сброса на дефолтную.
   - **История браузера:** Реализован обработчик `popstate` для плавной навигации по кнопкам браузера «Назад» и «Вперёд».
10. **Эргономика поиска, 3D-модель Chrono-Prism и университетский брендинг:**
    - **Единый Autocomplete Combobox:** Устранено неудобное разделение на два поля («Поиск» и нативный `<select>`). Внедрен единый компонент поиска-выбора: поле отображает текущую группу/преподавателя, при клике или вводе открывает стильный выпадающий список с мгновенной фильтрацией, кнопкой быстрой очистки `✕` и полной клавиатурной навигацией (стрелки, Enter, Escape).
    - **3D-модель Chrono-Prism с гироскопическими кольцами:** Разрозненные «парящие ступени» заменены на гармоничную модель хроно-сферы: кристаллический многогранник со внутренним ядром, два взаимопересекающихся орбитальных кольца времени (лазурно-голубое `#0284c7` и теплое оранжевое `#f97316`) и микроспутники учебных слотов с плавным параллаксом курсора.
    - **Официальный брендинг шапки:** Удалена лишняя плашка «Vue 3D Edition», логотип оформлен строго и профессионально: «КТМУ Расписание» с официальным подзаголовком «Колледж технологии, моделирования и управления».
11. **Адаптивная аутентификация мобильных устройств в локальной сети (LAN / Wi-Fi):**
    - **DynamicSessionInterface:** Мобильные браузеры (iOS Safari, Android Chrome) в соответствии с RFC 6265bis блокируют и удаляют cookie с флагом `Secure`, если сайт открыт по обычному `http://` к IP-адресу локальной сети (`http://192.168.x.x`). Реализован `DynamicSessionInterface` в `auth.py`, который динамически отключает флаг `Secure` для локальных HTTP-соединений по Wi-Fi, сохраняя полную защиту (`Secure=True`, `SameSite=Strict`) для HTTPS-окружения, Amvera и localhost.
12. **Скрытие аудиторий для дистанционных занятий:**
    - Для занятий с форматом `remote` («Дистант») или пар, проходящих онлайн, физическая аудитория отсутствует по смыслу занятия. Исключено отображение бейджа «Ауд. уточняется» и номеров кабинетов для дистанта — отображается только статус `Дистант`, исключая визуальный шум и логические противоречия.
13. **Отображение внешней площадки и корпусов занятий (Вознесенский пр., 44 / Садовая):**
    - Для очных занятий восстановлена маркировка внешней площадки (`↗ Вознесенский пр., 44`, `↗ ул. Садовая, 54`), основанная на флаге `room.isExternal` и префиксах кабинетов (`В-...`, `Возн`, `С-...`, `Садовая`).
    - Карточка пары отображает аккуратный лазурный бейдж площадки на отдельной аккуратной строке без переноса верхнего заголовка. Звонки соответствуют утверждённому расписанию группы (с поддержкой индивидуальных звонков `group.dayPeriodTimes` при их наличии).
14. **Оптимизация компоновки карточки, интерактивная навигация и очистка визуального шума:**
    - **Порядок времени и номера пары:** Время слота выведено на первое место жирным шрифтом (`10:10–11:40`), а номер пары — облегчённым (`· 2 пара`).
    - **Устранение переносов и перегруженности:** Убраны эмодзи дверей `🚪` у аудиторий и эмодзи из бейджей форматов, убран избыточный бейдж `Очно` для стандартных пар. Заголовок пары теперь всегда компактен и строго помещается в одну строку.
    - **Интерактивный переход к преподавателю:** Имя преподавателя в карточке группы преобразовано в кнопку-ссылку `@select-teacher`: при клике на преподавателя расписание мгновенно переключается на персональное расписание выбранного педагога.
    - **Очистка заголовков дней:** Убран счётчик количества пар (`3 пар`, `2 пар`) в заголовках колонок дней недели.
15. **Адаптивная верстка под мобильные устройства (iOS Safari / iPhone):**
    - **Устранение горизонтального скролла и переполнения контейнеров:** Добавлен `viewport-fit=cover` и `-webkit-text-size-adjust: 100%`, `overflow-x: hidden` на всех родительских уровнях (`html`, `body`, `#app`, `.main-content`).
    - **Ограничение WebKit 3D Context в `SpatialCard.vue`:** На touch-экранах и мобильных устройствах 3D-наклон отключается (`transform-style: flat`, `transform: none`), устраняя баг Safari, при котором элементы выпадали за пределы границ карточки.
    - **Гибкая сетка и переносы строк в карточках:** Для мобильных экранов внедрен `grid-template-columns: minmax(0, 1fr)`, автоматический перенос длинных названий дисциплин (`overflow-wrap: anywhere`), адаптивное сжатие отступов и безопасный flex-роутинг элементов, полностью исключающий выход контента за границы экрана iPhone.
16. **Интеллектуальный поиск на мобильных устройствах и защита от сброса группы (Enter Key Protection):**
    - **Нормализация поискового ввода:** Пользователи смартфонов часто вводят группы без дефисов или с пробелами (`1 кпд 3`, `1кпд3`, `кпд 3`), а также используют букву `е` вместо `ё`. Внедрена функция `normalizeSearchText()`, которая приводит запрос и названия к каноническому виду, обеспечивая мгновенный поиск при любом вводе с мобильной клавиатуры.
    - **Ранжирование результатов (Match Scoring):** Точные совпадения (включая нормализованные) получают наивысший приоритет (score 100) и поднимаются на первое место, префиксы получают score 80, подстроки — score 50.
    - **Защита от сброса при нажатии Enter / «Перейти» на iPhone:** При тапе в поле ввода и нажатии клавиши Enter/Go без поискового запроса расписание больше не переключается на первую группу в базе данных (`1-БИ-1`) — меню плавно закрывается, сохраняя выбранную группу. При активном поиске Enter выбирает точное или подсвеченное совпадение и скрывает экранную клавиатуру (`blur()`).
    - **Эргономика iOS Safari и предотвращение автозума:** Размер шрифта поля ввода на мобильных экранах установлен строго в 16px (устраняет непроизвольный 120% автозум iOS Safari при фокусе, ломавший верстку), для элементов списка задействован `@pointerdown.prevent` с безопасным тапом до скрытия клавиатуры, а для выпадающего меню включен `-webkit-overflow-scrolling: touch; touch-action: pan-y`.
17. **Пошаговая Drill-Down навигация (Специальность → Курс → Группа) и центральный оранжевый Chrono-Orb:**
    - **Отказ от текстового ввода в пользу крупных карточек:** Текстовый поиск заменен на нативный трехшаговый выбор (`GroupDrillDown.vue`): 1) Направление подготовки (с официальным шифром специальности `09.02.11`, `54.02.01` и числом групп); 2) Год поступления / курс (`2026`, `2025`, `2024`, `2023`); 3) Выбор студенческой группы в 1 клик. Это полностью исключило опечатки, неудобный ввод дефисов на смартфонах и ложные переключения.
    - **Фоновый оранжевый градиентный круг (Chrono-Orb) по центру страницы:** В компонентах `GroupDrillDown.vue` и `TeacherRoomPicker.vue` по центру экрана размещён эффектный фоновый световой круг с глубоким радиальным оранжевым градиентом (`#f97316` → `#ea580c` → мягкое свечение), контрастно выбивающийся из лазурно-серой палитры. Карточки оформлены в стиле полупрозрачного стекла (`backdrop-filter: blur(14px)`), благодаря чему теплый оранжевый фон мягко просвечивает сквозь карточки и между ними.
    - **Чистый заголовок вкладки:** Преписка `(Vue 3D)` удалена из тега `<title>`, название страницы стандартизировано: `Расписание — КТМУ`.
    - **Карточный выбор для преподавателей и аудиторий:** Реализован компонент `TeacherRoomPicker.vue` с алфавитным указателем педагогов и группировкой аудиторий по реальным корпусам (Цветочная, Вознесенский, Садовая).
18. **Ликвидация фризов главной страницы (Performance & Lag Optimization) и полная очистка эмодзи:**
    - **Устранение WebGL-нагрузки на главном экране:** Из экрана пошагового выбора исключён тяжелый 3D-баннер, что сократило вес публичного JS-бандла с 667 кБ до 122 кБ и обеспечило нативную плавность 60–120 FPS без нагрева и лагов на мобильных устройствах.
    - **Устранение Layout Thrashing (DOM Rect Caching):** Из обработчиков курсора `mousemove` в `use3DTilt.js` полностью исключены синхронные вызовы `getBoundingClientRect()`, вызывавшие принудительный пересчёт макета (reflow) на каждое движение указателя. Координаты кэшируются при `mouseenter` и `resize`.
    - **Мемоизация сортировки расписания (`lessonsByDay`):** В `ScheduleViewer.vue` повторяющиеся вызовы `getLessonsForDay(idx)` заменены на вычисляемое свойство `lessonsByDay`, предварительно сортирующее и группирующее пары один раз на смену данных вместо многократного `filter().sort()` в цикле рендера каждого дня.
    - **Полная очистка UI от эмодзи-смайликов:** Из всех кнопок (`[Сменить группу]`, `[Скачать CSV]`, `[Печать]`) и карточек удалены эмодзи-смайлики (`🔄`, `👥`, `👤`, `🎓`, `📅`, `☕`), заменены на строгие векторные SVG-иконки или чистые типографические лейблы.
19. **Паритет навигации ролей, отображение групп у преподавателей и официальный брендинг подвала:**
    - **Безупречное переключение видов (`Navbar.vue`):** Унифицированы параметры (`role`, `currentRole`) и события (`update:role`, `select-role`, `open-bells`, `open-subs`), обеспечив мгновенное переключение между каталогами «Группы», «Преподаватели» и «Аудитории».
    - **Отображение учебных групп в расписании педагога (`LessonCard.vue`):** В режиме преподавателя (`role === 'teacher'`) собственное ФИО преподавателя заменено на название учебной группы (`1-БИ-1`, `2-КПД-3` и потоковые группы `streamGroupIds`). Клик по группе мгновенно открывает расписание этой группы.
    - **Официальный брендинг КТМУ в подвале:** Исправлено наименование колледжа на «Колледж технологии, моделирования и управления (СПбГУПТД)», удален смайлик `🔐`.

Устаревшие архивные копии (`tools/frontend/`, дубликаты планировщиков и временные отчёты) удалены из кодовой базы для поддержания чистоты репозитория.
