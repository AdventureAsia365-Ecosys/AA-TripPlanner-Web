// AA-995 (spec §3.2, §4.1 row 2, §6.1) — the home hero: a curated static photo (public/hero/, built from an
// AA catalog photo, never from adventure.asia), an art-directed portrait crop below 768 px, AVIF + WebP,
// preloaded with fetchpriority=high (it is the LCP). It slides under the transparent header (-mt-header)
// and ends with the sentinel HeaderScrollState watches.
//
// Contrast: the text block's own scrim (.tp-hero-scrim) reaches --tp-scrim-text exactly where the text
// starts and only darkens below, so every line sits on >= .70 (white >= 6.8:1 over a white pixel;
// lib/contrast.test.ts). The settle (scale 1.06 -> 1, 4.8 s, once) runs only under data-motion="ok".
import type { CSSProperties, ReactNode } from "react";

// Photo: CIS catalog photo b934482d-0889-493f-b1f0-eee36ae0a704 (the China country cover, Forbidden City
// at golden hour), 1080 x 1080 source. Landscape = 16:9 crop (y 250..858), portrait = 7:10 crop (x 162..918).
export const HOME_HERO = {
  alt: "The Forbidden City in Beijing at golden hour",
  landscape: { widths: [640, 960, 1080], width: 1080, height: 608, position: "50% 45%" },
  portrait: { widths: [480, 756], width: 756, height: 1080, position: "50% 40%" },
} as const;

const PORTRAIT_MEDIA = "(max-width: 767px)";
const LANDSCAPE_MEDIA = "(min-width: 768px)";

export function heroSrcSet(kind: "land" | "port", widths: readonly number[], ext: "avif" | "webp"): string {
  return widths.map((w) => `/hero/home-${kind}-${w}.${ext} ${w}w`).join(", ");
}

export default function HomeHero({ children }: { children: ReactNode }) {
  const port = HOME_HERO.portrait;
  const land = HOME_HERO.landscape;
  return (
    <section className="tp-hero relative isolate -mt-header flex flex-col overflow-hidden bg-k-hero-bg">
      {/* React 19 hoists these to <head>. One per crop (media), AVIF only: a browser without AVIF skips
          the preload and fetches the WebP <source> as usual. */}
      <link rel="preload" as="image" type="image/avif" media={PORTRAIT_MEDIA} imageSrcSet={heroSrcSet("port", port.widths, "avif")}
            imageSizes="100vw" fetchPriority="high" />
      <link rel="preload" as="image" type="image/avif" media={LANDSCAPE_MEDIA} imageSrcSet={heroSrcSet("land", land.widths, "avif")}
            imageSizes="100vw" fetchPriority="high" />
      <picture>
        <source type="image/avif" media={PORTRAIT_MEDIA} srcSet={heroSrcSet("port", port.widths, "avif")} sizes="100vw"
                width={port.width} height={port.height} />
        <source type="image/webp" media={PORTRAIT_MEDIA} srcSet={heroSrcSet("port", port.widths, "webp")} sizes="100vw"
                width={port.width} height={port.height} />
        <source type="image/avif" srcSet={heroSrcSet("land", land.widths, "avif")} sizes="100vw" />
        <source type="image/webp" srcSet={heroSrcSet("land", land.widths, "webp")} sizes="100vw" />
        <img
          src={`/hero/home-land-${land.width}.webp`}
          alt={HOME_HERO.alt}
          width={land.width}
          height={land.height}
          fetchPriority="high"
          loading="eager"
          decoding="async"
          className="tp-hero-img absolute inset-0 -z-10 h-full w-full object-cover object-[var(--tp-hero-pos)] md:object-[var(--tp-hero-pos-md)]"
          style={{ "--tp-hero-pos": port.position, "--tp-hero-pos-md": land.position } as CSSProperties}
        />
      </picture>
      <div className="tp-hero-scrim tp-hero-text tp-on-dark mt-auto w-full">{children}</div>
      <span data-hero-end aria-hidden className="pointer-events-none absolute bottom-0 left-0 h-px w-px" />
    </section>
  );
}
