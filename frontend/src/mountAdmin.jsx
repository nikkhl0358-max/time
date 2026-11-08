import React from "react";
import ReactDOM from "react-dom/client";
import "./assets/base.css";
import "./admin-theme.css";
import App from "./App.jsx";

export function mountAdmin() {
  const root = document.getElementById("app");
  if (!root) return;
  ReactDOM.createRoot(root).render(<App />);
}
