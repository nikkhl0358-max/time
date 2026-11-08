/**
 * Безопасный клиент аутентификации.
 * Проверяет тип контента и корректно обрабатывает JSON и HTML ошибки.
 */
export async function authRequest(action, body) {
  const isPost = body !== undefined;
  const options = {
    method: isPost ? "POST" : "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: isPost ? { "Content-Type": "application/json" } : {},
    ...(isPost ? { body: JSON.stringify(body) } : {})
  };

  const response = await fetch(`/api/auth/${action}`, options);
  let result = null;
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    try {
      result = await response.json();
    } catch {
      result = null;
    }
  }

  if (!response.ok) {
    const errorMsg = result?.error || `Ошибка сервера (${response.status})`;
    throw new Error(errorMsg);
  }
  return result || {};
}
