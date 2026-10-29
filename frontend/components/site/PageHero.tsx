// AA-995 (spec §3.2, §4.2 row 2) — the photo hero of an inner page (destination pages): a catalog photo resolved to its
// CDN URL on the server (lib/photoResolve.ts, it is the LCP), or the no-photo hero (navy with the logo's mountain lines)
// when the country has no cover. Like the home hero it slides under the transparent header (-mt-header), its text sits
// on .tp-hero-scrim (>= --tp-scrim-text under every line, white >= 6.8:1 on a white pixel), it settles once under
// data-motion="ok" and ends with the sentinel HeaderScrollState watches. min-height, not height: a long country intro
// at 390 px grows the hero instead of clipping it.
import type { ReactNode } from "react";
import type { HeroPhoto } from "@/lib/photoResolve";
import LcpImage from "./LcpImage";
import { MountainMotif } from "./NoPhotoTile";

export default function PageHero({
  photo,
  alt,
  children,
}: {
  photo: HeroPhoto | null;
  alt: string;
  children: ReactNode;
}) {
  return (
    <section className={`tp-page-hero relative isolate -mt-header flex flex-col overflow-hidden ${photo ? "bg-k-hero-bg" : "bg-k-band"}`}>
      {photo ? (
        <>
          {/* React 19 hoists it to <head>: the CDN connection opens while the HTML is still arriving. */}
          {photo.preconnect && <link rel="preconnect" href={photo.preconnect} />}
          {/* A client component: no Flight preload hint, so a prefetch of this page elsewhere never downloads it. */}
          <LcpImage
            src={photo.src}
            srcSet={photo.srcSet ?? undefined}
            sizes="100vw"
            alt={alt}
            width={1600}
            height={900}
            fetchPriority="high"
            loading="eager"
            decoding="async"
            className="tp-hero-img absolute inset-0 -z-10 h-full w-full object-cover"
          />
        </>
      ) : (
        <MountainMotif className="-bottom-2 right-0 -z-10 w-4/5 text-k-band-accent md:w-1/2" />
      )}
      <div className="tp-hero-scrim tp-hero-text tp-on-dark mt-auto w-full">{children}</div>
      <span data-hero-end aria-hidden className="pointer-events-none absolute bottom-0 left-0 h-px w-px" />
    </section>
  );
}
