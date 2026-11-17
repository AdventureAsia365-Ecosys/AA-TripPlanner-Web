"use client";
// AA-1115 follow-up — the client boundary of Adventure Asia's 404 (app/not-found.tsx renders only this). Next serialises
// the root not-found into every page's RSC payload, an agency's pages included; behind this boundary that payload holds
// a reference to this module and the `preview` flag, never AA's words, and next/dynamic keeps the 404's body
// (./AaNotFoundPage.tsx) in a chunk of its own that a browser loads only when the 404 is actually shown. The server
// still renders the 404's HTML when it is shown (ssr stays on), so AA's 404 looks as before without JavaScript.
// Checked on the built app by scripts/check-agency-render.mjs (CI) and at source level by lib/agencyBrand.test.ts.
import dynamic from "next/dynamic";

const AaNotFoundPage = dynamic(() => import("./AaNotFoundPage"));

export default function AaNotFound({ preview }: { preview: boolean }) {
  return <AaNotFoundPage preview={preview} />;
}
