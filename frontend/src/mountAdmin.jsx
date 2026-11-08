import "./assets/base.css";
import "./admin-theme.css";
// Preserve original React entrypoint and its error boundary.
export async function mountAdmin() {
  await import("./main.jsx");
}
