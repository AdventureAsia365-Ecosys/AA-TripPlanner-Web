// AA-812 — inline script for <head>: apply the stored theme choice before first paint.
// Kept as a string (no imports at run time) so it can run before React hydrates.
// Every page follows it, the map planner (/plan) included since AA-837 (it was light-only before).
//
// AA-994 S5a (root ADR 0016 decision 4): the theme part is the kit's (ui/core/theme.ts, `createThemeStore` with the
// key `tp_theme`; the same model as the CIS admin and Booking: 'light' | 'dark' stored, absent = follow the OS).
// `themeStore` lives here, in a module without "use client", so the server layout can read its script; the theme
// toggle (ui/theme.ts) uses the same store.
//
// AA-995 — the motion gate: the same script sets <html data-motion="ok|reduce">. "reduce" when the OS asks
// for reduced motion (live: a `change` listener follows the setting) or the viewer stored
// localStorage tp_motion = "reduce". Every keyframe animation and every hidden "before reveal" state is
// scoped under html[data-motion="ok"] (app/globals.css), so without JS (no data-motion) the page is static
// and fully visible. Each storage read is in its own try/catch (blocked storage must not break the theme).
import { createThemeStore } from "./core/theme.ts";

export const THEME_STORAGE_KEY = "tp_theme";
export const MOTION_STORAGE_KEY = "tp_motion";

export const themeStore = createThemeStore({ storageKey: THEME_STORAGE_KEY });

const RESTORE_MOTION_SCRIPT = `(function(){var h=document.documentElement;try{var m=window.matchMedia('(prefers-reduced-motion: reduce)');var s=function(){var u=null;try{u=localStorage.getItem('tp_motion');}catch(e){}h.setAttribute('data-motion',m.matches||u==='reduce'?'reduce':'ok');};s();if(m.addEventListener){m.addEventListener('change',s);}}catch(e){h.setAttribute('data-motion','reduce');}})();`;

export const RESTORE_THEME_SCRIPT = `${themeStore.restoreScript.trim()}\n${RESTORE_MOTION_SCRIPT}`;
