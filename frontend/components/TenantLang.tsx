"use client";
// AA-1115 — keeps <html lang> on the agency's locale while an agency page is mounted (the layout's inline script does it
// on a document load; this covers a client-side navigation), and gives the root layout's "en" back when it unmounts,
// so an Adventure Asia page reached without a document load is not marked with the agency's language.
import { useLayoutEffect } from "react";

export default function TenantLang({ lang }: { lang: string }) {
  useLayoutEffect(() => {
    const html = document.documentElement;
    const before = html.lang;
    html.lang = lang;
    return () => {
      html.lang = before === lang ? "en" : before;
    };
  }, [lang]);
  return null;
}
