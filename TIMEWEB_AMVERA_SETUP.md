# Timeweb App Platform -> Amvera PostgreSQL: test-only deployment

This archive is **application source code** (not a database dump). It is intentionally a test site separate from the operational Timeweb `Timetable` project.

## Steps

1. Extract the ZIP and publish its files **at the repository root** of a new private Git repository. Timeweb App Platform reads source from Git; ZIP by itself is not a direct database import. `Dockerfile` must be at the repository root.
2. Timeweb -> `timetable-restore-test` project -> App Platform -> create Dockerfile-based application. Select this NEW repository and branch, Dockerfile path `Dockerfile`, context `.`. Do not reuse the production app or database.
3. Set `DATABASE_URL` as a **secret** (do not commit to Git):
   `postgresql://timetableuser:<URL_ENCODED_PASSWORD>@timetable-nikitakhlebnikov.tcp-waw0.amvera.tech:5432/timetabl?sslmode=require`
   Percent-encode special characters in password. External Amvera TCP access must be enabled.
4. Set `RESTORE_READ_ONLY=1`; `PGSSLMODE=require`; `PORT=5000` if Timeweb requests a container port. Select port 5000 in app settings, or make `PORT` match the setting.
5. Deploy. Open `/api/storage-diagnostics` before opening the UI. Expected: `{ "ok": true, "storage": "postgresql", "storageReady": true, "keyExists": true }`.
6. Check weeks, groups, teachers and schedules in the UI. All POST/PUT/PATCH/DELETE requests return HTTP 403 while RESTORE_READ_ONLY=1. This prevents app/API editing of the archive via web requests; background handlers and other indirect DB mutations are not fully audited. If strict physical read-only is essential, use a PostgreSQL read-only user or isolated copied DB.

## Security and limitations

- **Never put the database password in ZIP, Git, screenshots or chat.** A public source repo is not recommended.
- `DATABASE_URL` must reference the **Amvera** database `timetabl`, NOT Timeweb production PostgreSQL. The external hostname above is specific to the configured Amvera instance.
- No database schema or content is imported automatically. Application's compatibility layer may issue `CREATE TABLE IF NOT EXISTS` for supporting tables when opening a DB; existing tables and data are not intentionally replaced.
- The imported timetable JSON is ~26.7 MB. Provide enough instance memory; a small plan can OOM or time out during loading.
- The merged schedule's correctness, source references, publication history and runtime behavior remain to be verified, not guaranteed.
- If database connection fails, review `/api/storage-diagnostics` and Timeweb application logs. Do NOT click "create new storage" in the UI.
