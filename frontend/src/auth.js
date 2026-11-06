/** Сервер подтверждает вход до загрузки рабочего проекта; cookie недоступна JavaScript. */
export async function authRequest(action, body) {
  const response = await fetch(`/api/auth/${action}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Не удалось подтвердить вход");
  return result;
}
