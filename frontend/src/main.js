/** Public design selector and admin theme selector. The choice is local to the browser. */
const DESIGN_KEY = 'ktmu-design-version';
const readDesign = () => {
  try { return localStorage.getItem(DESIGN_KEY) === 'old' ? 'old' : 'new'; }
  catch { return 'new'; }
};
const isAdminRoute = () => location.pathname === '/admin' || location.pathname.startsWith('/admin/') || location.hash.startsWith('#admin');

async function bootstrapApplication() {
  const design = readDesign();
  const admin = isAdminRoute();
  const root = document.getElementById('app');
  if (!root) return;
  root.id = admin || design === 'old' ? 'root' : 'app';
  document.documentElement.dataset.design = design;
  document.documentElement.dataset.view = admin ? 'admin' : 'public';

  // Legacy React interface retains its original built-in styles; modern theme is opt-in.
  if (admin || design === 'old') {
    const { mountAdmin } = await import('./mountAdmin.jsx');
    await mountAdmin(design);
  } else {
    document.title = 'Расписание — КТМУ';
    const { mountVue } = await import('./mountVue.js');
    mountVue();
  }
  mountDesignSwitch(design);
}

function mountDesignSwitch(design) {
  const host = document.createElement('div');
  host.id = 'design-switcher';
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', 'Выбор оформления');
  const label = document.createElement('span');
  label.textContent = 'Дизайн:';
  host.append(label);
  for (const [value, title] of [['old', 'Старый'], ['new', 'Новый']]) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = title;
    btn.className = design === value ? 'design-choice active' : 'design-choice';
    btn.setAttribute('aria-pressed', String(design === value));
    btn.addEventListener('click', () => {
      if (value === design) return;
      try { localStorage.setItem(DESIGN_KEY, value); } catch {}
      location.reload();
    });
    host.append(btn);
  }
  document.body.append(host);
}

bootstrapApplication().catch(err => {
  console.error('Не удалось запустить интерфейс', err);
  const root = document.querySelector('#app, #root');
  if (root) root.textContent = 'Не удалось загрузить интерфейс. Обновите страницу.';
  mountDesignSwitch(readDesign());
});
