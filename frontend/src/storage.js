// Замена window.storage (доступной только внутри артефактов Claude) на настоящий
// backend API. Тот же смысл: get(key) / set(key, value), данные общие для всех.

export async function storageGet(key) {
  try {
    const res = await fetch(`/api/storage/${encodeURIComponent(key)}`);
    if (!res.ok) return null; // 404 — ключа ещё нет, это нормально при первом запуске
    return await res.json(); // { key, value }
  } catch (e) {
    console.error("storageGet failed", e);
    return null;
  }
}

export async function storageMeta(key) {
  try {
    const res = await fetch(`/api/storage-meta/${encodeURIComponent(key)}`, {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache" },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error("storageMeta failed", e);
    return null;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Строгая загрузка с повторными попытками. Во время redeploy reverse-proxy Amvera
// может несколько секунд отвечать 502/503 или оборвать запрос, хотя рабочий storage
// на постоянном диске никуда не делся. Такие ответы нельзя трактовать как отсутствие данных.
export async function storageGetStrict(key, options = {}) {
  const attempts = Math.max(1, Number(options.attempts || 8));
  const delayMs = Math.max(100, Number(options.delayMs || 850));
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const qs = options.editorCompact ? "?editor=1" : "";
      const res = await fetch(`/api/storage/${encodeURIComponent(key)}${qs}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
      if (res.status === 404) return { key, value: null, missing: true, status: 404 };

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const err = new Error(detail?.error || `Ошибка загрузки данных: HTTP ${res.status}`);
        err.status = res.status;
        // 5xx/429/425 могут быть переходным состоянием при пересборке.
        if ([425, 429, 500, 502, 503, 504].includes(res.status) && attempt < attempts) {
          lastError = err;
          await sleep(delayMs * attempt);
          continue;
        }
        throw err;
      }

      let payload;
      try {
        payload = await res.json();
      } catch (e) {
        const err = new Error("Сервер хранения вернул неполный ответ");
        if (attempt < attempts) {
          lastError = err;
          await sleep(delayMs * attempt);
          continue;
        }
        throw err;
      }

      if (!payload || typeof payload !== "object" || !("value" in payload)) {
        const err = new Error("Сервер хранения вернул ответ без поля данных");
        if (attempt < attempts) {
          lastError = err;
          await sleep(delayMs * attempt);
          continue;
        }
        throw err;
      }

      // Пустой существующий ключ тоже не считаем «новой базой». Повторяем запрос,
      // а затем отдаём отдельный признак, чтобы пользователь мог восстановить backup.
      if (payload.value === null || payload.value === "") {
        if (attempt < attempts) {
          lastError = new Error("Ключ хранилища временно пуст");
          await sleep(delayMs * attempt);
          continue;
        }
        return { ...payload, empty: true, status: 200 };
      }
      return { ...payload, status: 200 };
    } catch (e) {
      lastError = e;
      if (attempt < attempts) {
        await sleep(delayMs * attempt);
        continue;
      }
    }
  }
  throw lastError || new Error("Сервер хранения недоступен");
}

export async function storageVersionGet(key, versionId) {
  const res = await fetch(`/api/storage-version/${encodeURIComponent(key)}/${encodeURIComponent(versionId)}`, { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось загрузить версию: HTTP ${res.status}`);
  return data?.version || null;
}

export async function storageBackups() {
  const res = await fetch("/api/storage-backups", { cache: "no-store" });
  if (!res.ok) throw new Error(`Не удалось получить резервные копии: HTTP ${res.status}`);
  const data = await res.json();
  return Array.isArray(data?.backups) ? data.backups : [];
}

export async function storageDownloadBackup(name, userId) {
  const res = await fetch(`/api/storage-backups/${encodeURIComponent(name)}/download`, {
    cache: "no-store",
    headers: { "X-Admin-User-Id": String(userId || "") },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.error || `Не удалось скачать резервную копию: HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export async function storageRestoreBackup(name) {
  const res = await fetch(`/api/storage-backups/${encodeURIComponent(name)}/restore`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось восстановить резервную копию: HTTP ${res.status}`);
  return data;
}

export async function storageSet(key, value, options = {}) {
  const qs = new URLSearchParams();
  if (options.allowCreate) qs.set("allow_create", "1");
  qs.set("raw", "1");
  const url = `/api/storage/${encodeURIComponent(key)}?${qs.toString()}`;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  const attempts = Math.max(1, Number(options.attempts || 3));
  const timeoutMs = Math.max(5000, Number(options.timeoutMs || 60000));

  let body = text;
  const headers = { "Content-Type": "application/json; charset=utf-8" };
  if (options.baseToken) headers["X-Base-Token"] = String(options.baseToken);
  if (text.length > 128 * 1024 && typeof CompressionStream !== "undefined") {
    try {
      const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
      body = new Uint8Array(await new Response(stream).arrayBuffer());
      headers["Content-Encoding"] = "gzip";
    } catch (e) {
      console.warn("gzip save compression unavailable, using plain payload", e);
      body = text;
    }
  }

  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method: "POST", headers, body, signal: controller.signal });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(data?.error || `Ошибка сохранения: HTTP ${res.status}`);
        err.status = res.status;
        if ([425, 429, 500, 502, 503, 504].includes(res.status) && attempt < attempts) {
          lastError = err;
          await sleep(600 * attempt);
          continue;
        }
        throw err;
      }
      return data;
    } catch (e) {
      lastError = e?.name === "AbortError" ? new Error("Сохранение не завершилось за отведённое время") : e;
      if (attempt < attempts) {
        await sleep(600 * attempt);
        continue;
      }
    } finally {
      clearTimeout(timer);
    }
  }
  console.error("storageSet failed", lastError);
  throw lastError || new Error("Не удалось сохранить данные");
}



// v1498: generic atomic page-section merge. Each admin page sends only the
// sections it owns, together with the exact base snapshot used to make the edit.
export async function storageSectionMerge(key, base, local, meta = {}) {
  const res = await fetch(`/api/storage-section-merge/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, local, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка совместного сохранения страницы: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// v1489: atomic server-side merge for simultaneous timetable editing.
// Different groups, days, slots and assignments are merged under the same storage lock.
// v1565: immediate atomic patch for one or a few timetable assignments.
// Manual cell placement uses this before the debounced page autosave, so a stale
// full-page request cannot make a freshly placed lesson disappear.
export async function storageSchedulePatch(key, patch = {}, meta = {}) {
  const res = await fetch(`/api/storage-schedule-patch/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patch, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка быстрого сохранения ячейки: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}



export async function storagePublicationCommit(key, payload = {}, meta = {}) {
  const res = await fetch(`/api/storage-publication-commit/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка фиксации публикации: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function storagePublishWeeks(key, weeks = [], meta = {}) {
  const res = await fetch(`/api/storage-publish-weeks/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weeks, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка публикации недель: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function storageScheduleGroupMerge(key, groupId, base, local, meta = {}) {
  // v1646: one logical autosave keeps the same request id across a short retry.
  // Retry only transport/5xx failures; validation/conflict responses are never
  // replayed automatically.
  const requestId = meta?.requestId || (globalThis.crypto?.randomUUID?.() || `grp-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const payloadMeta = { ...meta, requestId };
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const res = await fetch(`/api/storage-schedule-group-merge/${encodeURIComponent(key)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ groupId, base, local, meta: payloadMeta }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(data?.error || `Ошибка сохранения расписания группы: HTTP ${res.status}`);
        err.status = res.status;
        if (res.status < 500 || attempt > 0) throw err;
        lastError = err;
      } else {
        return data;
      }
    } catch (err) {
      lastError = err;
      const status = Number(err?.status || 0);
      if ((status && status < 500) || attempt > 0) throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  throw lastError || new Error("Не удалось сохранить расписание группы");
}

export async function storageScheduleMerge(key, base, local, meta = {}) {
  const res = await fetch(`/api/storage-schedule-merge/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, local, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка совместного сохранения расписания: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}


// v1478: atomic server-side merge for simultaneous graph editing.
// The server applies only the fields/weeks changed relative to `base` while
// holding the storage lock, so debounce order between browsers cannot erase
// another employee's graph edits.
export async function storageGraphMerge(key, base, local, meta = {}) {
  const res = await fetch(`/api/storage-graph-merge/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, local, meta }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка совместного сохранения графиков: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}



// v1533: lightweight graph autosave. Only changed graph fields are sent and
// only the merged rows come back, instead of the whole semester project.
export async function storageGraphPatch(key, changes = [], historyAppend = [], meta = {}, schedulePatch = null) {
  const res = await fetch(`/api/storage-graph-patch/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changes, historyAppend, meta, schedulePatch }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error || `Ошибка быстрого сохранения графиков: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function presenceHeartbeat(payload) {
  try {
    const res = await fetch("/api/presence/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.active || [];
  } catch (e) {
    console.error("presenceHeartbeat failed", e);
    return [];
  }
}

export async function presenceGet() {
  try {
    const res = await fetch("/api/presence");
    if (!res.ok) return [];
    const data = await res.json();
    return data.active || [];
  } catch (e) {
    console.error("presenceGet failed", e);
    return [];
  }
}

export function presenceLeave(sessionId) {
  if (!sessionId) return;
  const body = JSON.stringify({ sessionId });
  try {
    if (typeof navigator !== "undefined" && navigator.sendBeacon) {
      navigator.sendBeacon("/api/presence/leave", new Blob([body], { type: "application/json" }));
      return;
    }
    fetch("/api/presence/leave", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
  } catch { /* TTL всё равно снимет статус */ }
}

export async function serverGenerateStart(payload = {}) {
  // Only a tiny command is sent. The server reads the already saved project
  // snapshot itself, avoiding HTTP 413 on large semesters.
  const res = await fetch('/api/schedule/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ storageKey: payload.storageKey || 'schedule-data-v2', groupId: payload.groupId || '', fastMode: !!payload.fastMode, weekNumber: Number(payload.weekNumber || 0) }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось запустить серверный расчёт: HTTP ${res.status}`);
  return data;
}

export async function serverGenerateStatus(jobId, afterProgress = 0) {
  const suffix = Number(afterProgress) > 0 ? `?after_progress=${encodeURIComponent(Number(afterProgress))}` : '';
  const res = await fetch(`/api/schedule/generate/${encodeURIComponent(jobId)}${suffix}`, { cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось получить статус расчёта: HTTP ${res.status}`);
  return data;
}

export async function serverGenerateCancel(jobId) {
  const res = await fetch(`/api/schedule/generate/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось остановить расчёт: HTTP ${res.status}`);
  return data;
}

export async function serverAutoGraphStart(payload = {}) {
  const res = await fetch('/api/graphs/auto-group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      storageKey: payload.storageKey || 'schedule-data-v2',
      groupId: payload.groupId || '',
      overloadMode: payload.overloadMode || 'strict',
      overloadTolerance: Number(payload.overloadTolerance) || 0,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось запустить автографик: HTTP ${res.status}`);
  return data;
}

export async function serverAutoGraphStatus(jobId) {
  const res = await fetch(`/api/graphs/auto-group/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Не удалось получить статус автографика: HTTP ${res.status}`);
  return data;
}


export async function publicIndexGet() {
  try {
    const res = await fetch('/api/public-index', { cache: 'default' });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error('publicIndexGet failed', e);
    return null;
  }
}

export async function publicScheduleGet(kind, id, options = {}) {
  try {
    const safeKind = kind === 'teacher' ? 'teacher' : 'group';
    const scope = options?.scope === 'week' && Number(options?.week) > 0 ? 'week' : 'semester';
    const qs = scope === 'week' ? `?scope=week&week=${encodeURIComponent(Number(options.week))}` : '?scope=semester';
    const res = await fetch(`/api/public-schedule/${safeKind}/${encodeURIComponent(String(id || ''))}${qs}`, { cache: 'default' });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error('publicScheduleGet failed', e);
    return null;
  }
}

export async function publicStatusGet() {
  try {
    const res = await fetch('/api/public-status', { cache: 'default' });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error('publicStatusGet failed', e);
    return null;
  }
}

export async function publicBootstrapGet() {
  try {
    // v1628: публичный bootstrap имеет короткий HTTP cache + ETag на сервере.
    // Не запрещаем браузеру использовать его: повторные открытия заметно быстрее.
    const res = await fetch('/api/public-bootstrap', { cache: 'no-cache' });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error('publicBootstrapGet failed', e);
    return null;
  }
}
