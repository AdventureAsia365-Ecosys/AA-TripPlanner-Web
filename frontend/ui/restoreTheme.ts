// AA-812 — inline script for <head>: apply the stored theme choice before first paint.
// Kept as a string (no imports) so it can run before React hydrates.
// Every page follows it, the map planner (/plan) included since AA-837 (it was light-only before).
//
// AA-995 — the motion gate: the same script sets <html data-motion="ok|reduce">. "reduce" when the OS asks
// for reduced motion (live: a `change` listener follows the setting) or the viewer stored
// localStorage tp_motion = "reduce". Every keyframe animation and every hidden "before reveal" state is
// scoped under html[data-motion="ok"] (app/globals.css), so without JS (no data-motion) the page is static
// and fully visible. Each storage read is in its own try/catch (blocked storage must not break the theme).
export const THEME_STORAGE_KEY = "tp_theme";
export const MOTION_STORAGE_KEY = "tp_motion";

export const RESTORE_THEME_SCRIPT = `(function(){var h=document.documentElement;try{var c=localStorage.getItem('tp_theme');var d=c==='dark'||(c!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);h.setAttribute('data-theme',d?'dark':'light');}catch(e){h.setAttribute('data-theme','light');}try{var m=window.matchMedia('(prefers-reduced-motion: reduce)');var s=function(){var u=null;try{u=localStorage.getItem('tp_motion');}catch(e){}h.setAttribute('data-motion',m.matches||u==='reduce'?'reduce':'ok');};s();if(m.addEventListener){m.addEventListener('change',s);}}catch(e){h.setAttribute('data-motion','reduce');}})();`;
