"use client";
// AA-995 — the home hero picture and its preloads (moved out of HomeHero.tsx in S256, mobile LCP): a client component,
// so React Flight emits no image preload hints for it. Rendered by a Server Component, these preloads travelled in
// the RSC payload of "/" and every prefetch of "/" from another page downloaded the hero (60 KB AVIF, high priority)
// next to that page's own LCP image (components/site/LcpImage.tsx has the full story). Server-rendered HTML is
// unchanged: React DOM SSR still hoists the two preloads to <head> and renders the picture.
import type { CSSProperties } from "react";

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

export default function HomeHeroPicture() {
  const port = HOME_HERO.portrait;
  const land = HOME_HERO.landscape;
  return (
    <>
      {/* React 19 hoists these to <head>. One per crop (media), AVIF only: a browser without AVIF skips
          the preload and fetches the WebP <source> as usual. */}
      <link rel="preload" as="image" type="image/avif" media={PORTRAIT_MEDIA} href={`/hero/home-port-${port.width}.avif`} imageSrcSet={heroSrcSet("port", port.widths, "avif")}
            imageSizes="100vw" fetchPriority="high" />
      <link rel="preload" as="image" type="image/avif" media={LANDSCAPE_MEDIA} href={`/hero/home-land-${land.width}.avif`} imageSrcSet={heroSrcSet("land", land.widths, "avif")}
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
    </>
  );
}
