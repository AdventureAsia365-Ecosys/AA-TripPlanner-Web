// AA-1041 (R7 item 6) — the share / print view always prints in the light theme.
//
// Every k-* class reads an `--aa-*` variable of ui/core/tokens.css, and <html data-theme="dark"> turns those into light
// text on dark surfaces. Browsers drop background colours when printing, so a traveller on the dark theme would print
// near-white text on white paper. The `--aa-*` values may only be defined in the kit core (check-kit-copy.mjs --css),
// so instead of a print stylesheet that redefines them, the page switches <html> to the light theme while it prints:
// on `beforeprint` (the browser's print, Ctrl+P, the "Print or save as PDF" button) and when the `print` media query
// starts to match (also what print emulation in a test drives), and puts the viewer's theme back afterwards. The
// stored theme choice is never touched, and the tenant brand block (ui/core/brandTheme.ts) follows the light theme.
// Transitions are off while the theme flips (html[data-theme-switching], app/globals.css), so nothing prints mid-fade.
// Plain DOM types only, so node --test runs it with a fake window (lib/printTheme.test.ts).

/** The bits of `window` this module uses. */
export interface PrintWindow {
  document: { documentElement: { getAttribute(n: string): string | null; setAttribute(n: string, v: string): void; removeAttribute(n: string): void } };
  addEventListener(type: "beforeprint" | "afterprint", fn: () => void): void;
  removeEventListener(type: "beforeprint" | "afterprint", fn: () => void): void;
  matchMedia?: (query: string) => {
    matches: boolean;
    addEventListener?: (type: "change", fn: () => void) => void;
    removeEventListener?: (type: "change", fn: () => void) => void;
  };
}

/** Marks <html> while this module holds the light theme for printing. */
export const PRINT_THEME_ATTR = "data-print-theme";

/** Starts following print; returns the cleanup (which also puts the viewer's theme back). */
export function printInLightTheme(win: PrintWindow): () => void {
  const html = win.document.documentElement;
  let saved: string | null = null;
  let held = false;

  const enter = () => {
    if (held) return;
    const theme = html.getAttribute("data-theme");
    if (theme !== "dark") return;
    held = true;
    saved = theme;
    html.setAttribute("data-theme-switching", "");
    html.setAttribute(PRINT_THEME_ATTR, "light");
    html.setAttribute("data-theme", "light");
  };
  const leave = () => {
    if (!held) return;
    held = false;
    // The viewer may have changed the theme meanwhile (another tab): only undo what this module set.
    if (html.getAttribute("data-theme") === "light" && saved) html.setAttribute("data-theme", saved);
    html.removeAttribute(PRINT_THEME_ATTR);
    html.removeAttribute("data-theme-switching");
    saved = null;
  };

  const mq = win.matchMedia?.("print");
  const onMedia = () => (mq?.matches ? enter() : leave());
  win.addEventListener("beforeprint", enter);
  win.addEventListener("afterprint", leave);
  mq?.addEventListener?.("change", onMedia);
  if (mq?.matches) enter();

  return () => {
    win.removeEventListener("beforeprint", enter);
    win.removeEventListener("afterprint", leave);
    mq?.removeEventListener?.("change", onMedia);
    leave();
  };
}
