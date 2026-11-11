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

function isClassicPreferred() {
  try {
    const params = new URLSearchParams(window.location.search);
    const view = params.get("view");
    if (view === "classic") return true;
    if (view === "modern") {
      localStorage.removeItem("ktmu_schedule_ui_version");
      return false;
    }
    return localStorage.getItem("ktmu_schedule_ui_version") === "classic";
  } catch (_) {
    return false;
  }
}

async function bootstrapApplication() {
  if (isAdminRoute()) {
    const adminModulePromise = import("./mountAdmin.jsx");
    try {
      const res = await fetch("/api/auth/session");
      const data = res.ok ? await res.json() : null;
      if (data?.user) {
        const m = await adminModulePromise;
        m.mountAdmin();
        return;
      }
    } catch (_) {}
    window.location.replace("/?auth=admin");
    return;
  }
  if (isClassicPreferred()) {
    const m = await import("./mountAdmin.jsx");
    m.mountAdmin();
    return;
  }
  document.title = "Расписание — КТМУ";
  const m = await import("./mountVue.js");
  m.mountVue();
}

bootstrapApplication();
