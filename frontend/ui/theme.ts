"use client";
// AA-812 — light / dark / system theme, the same model as the CIS kit (app/_kit/theme.ts):
// localStorage `tp_theme` holds the CHOICE ('light' | 'dark'; absent = follow the OS); the
// RESOLVED theme goes to <html data-theme>, which drives the --aa-* variables in ui/tokens.css.
// The restore script in app/layout.tsx applies it before first paint (no flash).

import { useSyncExternalStore } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY = "tp_theme";

function readChoice(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function resolve(choice: ThemeChoice): "light" | "dark" {
  if (choice !== "system") return choice;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const listeners = new Set<() => void>();

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onMedia = () => {
    if (readChoice() === "system") {
      applyTheme(resolve("system"));
      cb();
    }
  };
  mq.addEventListener("change", onMedia);
  return () => {
    listeners.delete(cb);
    mq.removeEventListener("change", onMedia);
  };
}

// AA-995 (map-first graft): no colour transitions while the theme flips, so cards and buttons do not
// fade one by one. app/globals.css turns transitions off while html[data-theme-switching] is set.
function applyTheme(theme: "light" | "dark"): void {
  const root = document.documentElement;
  root.setAttribute("data-theme-switching", "");
  root.setAttribute("data-theme", theme);
  requestAnimationFrame(() => requestAnimationFrame(() => root.removeAttribute("data-theme-switching")));
}

export function setThemeChoice(choice: ThemeChoice): void {
  try {
    if (choice === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, choice);
  } catch {
    /* storage blocked: still apply for this page view */
  }
  applyTheme(resolve(choice));
  listeners.forEach((l) => l());
}

export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(subscribe, readChoice, () => "system" as ThemeChoice);
}
