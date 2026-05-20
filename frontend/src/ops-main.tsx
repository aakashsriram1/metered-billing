import React from "react";
import { createRoot } from "react-dom/client";
import { OpsConsoleApp } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <OpsConsoleApp />
  </React.StrictMode>,
);
