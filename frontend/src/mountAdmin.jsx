// The administrative panel and the old public version always run the original React application.
// Never import admin-theme.css: public design preferences must not alter the admin UI.
export async function mountAdmin() {
  await import('./main.jsx');
}
