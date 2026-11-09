// Run the original React application for old public mode and for all admin routes.
export async function mountAdmin(design = 'old') {
  if (design === 'new') {
    // The new admin theme is not loaded when the legacy theme is selected.
    await import('./admin-theme.css');
  }
  await import('./main.jsx');
}
