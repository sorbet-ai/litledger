import "@fontsource-variable/fraunces/full.css";
import "@fontsource-variable/fraunces/full-italic.css";
import "@fontsource-variable/nunito-sans/wght.css";
import "@fontsource-variable/nunito-sans/wght-italic.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./app/App";
import { applyTheme, savedTheme } from "./lib/storage";
import "./styles.css";

// Apply the saved theme before first paint.
applyTheme(savedTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
