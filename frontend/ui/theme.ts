"use client";
// AA-812 — light / dark / system theme for the theme toggle. AA-994 S5a: the state is the kit core's theme store
// (ui/core/theme.ts `createThemeStore`, made in ui/restoreTheme.ts with the key `tp_theme`, the same store whose
// restore script runs in app/layout.tsx): the key holds the CHOICE ('light' | 'dark'; absent = follow the OS), the
// RESOLVED theme goes to <html data-theme>, which drives the --aa-* variables of ui/core/tokens.css. The store also
// follows the OS while on "system" and the choice made in another tab.
// TripPlanner keeps two things on top: blocked storage never throws out of the toggle, and colours do not fade one by
// one while the theme flips (data-theme-switching, below).

import { useSyncExternalStore } from "react";
import { themeStore, THEME_STORAGE_KEY } from "./restoreTheme";
import type { ThemeChoice } from "./core/theme";

export type { ThemeChoice };
export const THEME_KEY = THEME_STORAGE_KEY;

function readChoice(): ThemeChoice {
  try {
    return themeStore.getSnapshot();
  } catch {
    return "system"; // storage blocked: the restore script already applied the OS theme
  }
}

// AA-995 (map-first graft): no colour transitions while the theme flips, so cards and buttons do not
// fade one by one. app/globals.css turns transitions off while html[data-theme-switching] is set.
export function setThemeChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  root.setAttribute("data-theme-switching", "");
  try {
    themeStore.setChoice(choice);
  } catch {
    themeStore.applyResolvedTheme(choice); // storage blocked: still apply for this page view
  }
  requestAnimationFrame(() => requestAnimationFrame(() => root.removeAttribute("data-theme-switching")));
}

export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(themeStore.subscribe, readChoice, themeStore.getServerSnapshot);
}
