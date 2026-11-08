function isAdminRoute() {
  const path = window.location.pathname;
  const hash = window.location.hash;
  return path.startsWith("/admin") || hash.startsWith("#admin");
}

function bootstrapApplication() {
  if (isAdminRoute()) {
    import("./mountAdmin.jsx").then(m => m.mountAdmin());
  } else {
    import("./mountVue.js").then(m => m.mountVue());
  }
}

bootstrapApplication();
