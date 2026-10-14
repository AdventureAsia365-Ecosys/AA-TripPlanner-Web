// AA-812 — inline script for <head>: apply the stored theme choice before first paint.
// Kept as a string (no imports) so it can run before React hydrates.
// The map planner (/plan) is light-only (hex palette, browser-styled form controls), so it always
// gets data-theme="light" there; PlannerApp also pins it on client-side navigation.
export const RESTORE_THEME_SCRIPT = `(function(){try{if(/^\\/plan(\\/|$)/.test(location.pathname)){document.documentElement.setAttribute('data-theme','light');return;}var c=localStorage.getItem('tp_theme');var d=c==='dark'||(c!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.setAttribute('data-theme',d?'dark':'light');}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`;
