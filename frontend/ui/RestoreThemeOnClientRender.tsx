"use client";
// AA-1038 — the theme and motion of a page that React renders in the browser instead of hydrating it.
//
// A page that calls notFound() (or throws) while it renders on the server, outside any Suspense boundary, does not get
// its HTML: Next 16 answers the bare error shell (<html id="__next_error__">, empty body) with the status code and the
// RSC payload, and the browser renders the whole tree from it. app/[tenant] is such a page (an unknown /{slug} is the
// 404 page only after its async tenant lookup). In that render React takes over the existing <html>: it drops every
// attribute the restore script set and writes the layout's server value (data-theme="light"), and the layout's inline
// <script> is inserted as a node but never runs. A dark visitor would get the light 404 page.
//
// This component runs the same restore script (ui/restoreTheme.ts) once, in a layout effect, so before the first
// paint. It only acts when the restore script has not applied to the current <html> (no data-motion: the script always
// sets it). On a normal hydrated page the attributes are kept, so it does nothing and the motion listener of the head
// script is not added twice.
import { useLayoutEffect } from "react";
import { RESTORE_THEME_SCRIPT } from "./restoreTheme";

export default function RestoreThemeOnClientRender() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.hasAttribute("data-motion")) return;
    const script = document.createElement("script");
    script.text = RESTORE_THEME_SCRIPT;
    document.head.appendChild(script); // an inline script added to the DOM runs at once, synchronously
    script.remove();
  }, []);
  return null;
}
