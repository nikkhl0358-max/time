/**
  main.js
  Точка входа приложения расписания КТМУ.
  Проверяет авторизацию для маршрута /admin: при активной сессии монтирует
  полнофункциональный редактор расписания, иначе открывает современную
  авторизацию в интерфейсе Vue.
*/

function isAdminRoute() {
  const path = window.location.pathname;
  const hash = window.location.hash;
  return path.startsWith("/admin") || hash.startsWith("#admin");
}

async function bootstrapApplication() {
  if (isAdminRoute()) {
    try {
      const res = await fetch("/api/auth/session");
      const data = res.ok ? await res.json() : null;
      if (data?.user) {
        const m = await import("./mountAdmin.jsx");
        m.mountAdmin();
        return;
      }
    } catch (_) {}
    window.location.replace("/?auth=admin");
    return;
  }
  document.title = "Расписание — КТМУ";
  const m = await import("./mountVue.js");
  m.mountVue();
}

bootstrapApplication();
