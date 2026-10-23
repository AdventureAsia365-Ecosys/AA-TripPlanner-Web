// AA-812 — inline script for <head>: apply the stored theme choice before first paint.
// Kept as a string (no imports) so it can run before React hydrates.
// Every page follows it, the map planner (/plan) included since AA-837 (it was light-only before).
export const RESTORE_THEME_SCRIPT = `(function(){try{var c=localStorage.getItem('tp_theme');var d=c==='dark'||(c!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.setAttribute('data-theme',d?'dark':'light');}catch(e){document.documentElement.setAttribute('data-theme','light');}})();`;
