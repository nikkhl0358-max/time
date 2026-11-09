/** Public design selection. Admin pages always load the legacy React application. */
const KEY = 'ktmu-design-version';
function isAdminRoute() {
  const path = window.location.pathname;
  return path === '/admin' || path.startsWith('/admin/') || window.location.hash.startsWith('#admin');
}
function readDesign() {
  try { return localStorage.getItem(KEY) === 'old' ? 'old' : 'new'; }
  catch (_) { return 'new'; }
}
async function bootstrap() {
  const admin = isAdminRoute();
  const design = readDesign();
  document.documentElement.dataset.design = admin ? 'old' : design;
  document.documentElement.dataset.view = admin ? 'admin' : 'public';
  const root = document.getElementById('app');
  if (!root) throw new Error('Корневой элемент приложения не найден');
  if (admin || design === 'old') {
    root.id = 'root';
    const { mountAdmin } = await import('./mountAdmin.jsx');
    await mountAdmin();
    if (!admin) mountReturnToNewDesign();
  } else {
    const { mountVue } = await import('./mountVue.js');
    mountVue();
  }
}
function mountReturnToNewDesign() {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'return-new-design';
  btn.textContent = 'Новый дизайн';
  btn.title = 'Вернуться к новому оформлению расписания';
  btn.onclick = () => {
    try { localStorage.setItem(KEY, 'new'); } catch (_) {}
    window.location.assign('/');
  };
  document.body.appendChild(btn);
}
bootstrap().catch(error => {
  console.error('Ошибка запуска интерфейса:', error);
  const root = document.querySelector('#app, #root');
  if (root) root.textContent = 'Не удалось загрузить расписание. Обновите страницу.';
});
