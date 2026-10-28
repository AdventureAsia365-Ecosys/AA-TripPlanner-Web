// AA-995 (spec §3.2, §4.1 row 2, §6.1) — the home hero: a curated static photo (public/hero/, built from an
// AA catalog photo, never from adventure.asia), an art-directed portrait crop below 768 px, AVIF + WebP,
// preloaded with fetchpriority=high (it is the LCP). It slides under the transparent header (-mt-header)
// and ends with the sentinel HeaderScrollState watches.
//
// Contrast: the text block's own scrim (.tp-hero-scrim) reaches --tp-scrim-text exactly where the text
// starts and only darkens below, so every line sits on >= .70 (white >= 6.8:1 over a white pixel;
// lib/contrast.test.ts). The settle (scale 1.06 -> 1, 4.8 s, once) runs only under data-motion="ok".
import type { ReactNode } from "react";
import HomeHeroPicture from "./HomeHeroPicture";

export default function HomeHero({ children }: { children: ReactNode }) {
  return (
    <section className="tp-hero relative isolate -mt-header flex flex-col overflow-hidden bg-k-hero-bg">
      <HomeHeroPicture />
      <div className="tp-hero-scrim tp-hero-text tp-on-dark mt-auto w-full">{children}</div>
      <span data-hero-end aria-hidden className="pointer-events-none absolute bottom-0 left-0 h-px w-px" />
    </section>
  );
}
