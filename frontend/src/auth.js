/** Сервер подтверждает вход до загрузки рабочего проекта; cookie недоступна JavaScript. */
export async function authRequest(action, body) {
  const response = await fetch(`/api/auth/${action}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  // A proxy can return HTML instead of JSON on 502/503/504. Show the actual
  // HTTP problem rather than leaving the login dialog apparently unresponsive.
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    const reason = result?.error || (response.status >= 500
      ? `Сервер временно недоступен (HTTP ${response.status}). Повторите попытку позже.`
      : `Не удалось подтвердить вход (HTTP ${response.status})`);
    throw new Error(reason);
  }
  if (!result || typeof result !== "object") throw new Error("Некорректный ответ сервера авторизации");
  return result;
}
