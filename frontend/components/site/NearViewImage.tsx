"use client";
// AA-995 (S258, mobile LCP) — a below-the-fold image that is requested only when it comes near the viewport.
//
// Why not just loading="lazy": Chrome starts a lazy image when it is within 1250–2500 px of the viewport, so on a
// phone the destination page's static map (Mapbox, ~300 KB) and the place thumbnails (catalog 302 → CDN, 600 w files
// for a 56 px slot, ~480 KB in all) were all fetched while the page was still loading. Production Lighthouse
// (S258) showed them in the LCP window whenever the first paint came late: the hero photo itself was ready, but the
// simulation counted ~780 KB of below-the-fold images before it. Here the request starts only when the image is
// within NEAR_VIEW_MARGIN of the viewport.
//
// Server HTML: an <img> without src (its classes keep the layout box, so no shift when the photo arrives) plus a
// <noscript> copy with the real src; without JS the empty element is hidden by globals.css (no data-motion on <html>).
// The alt text of the pending element is transparent, so it never flashes on screen before the image.
import { useEffect, useRef, useState, type ImgHTMLAttributes } from "react";

// Below the viewport only; small enough that nothing under the destination hero's first screen starts at load on a
// phone (the places map sits ~590 px under a 412 x 823 viewport), large enough to start before the image is seen.
export const NEAR_VIEW_MARGIN = "0px 0px 300px 0px";

export type NearViewImageProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "loading"> & {
  src: string;
  alt: string;
};

export default function NearViewImage({ src, alt, className, ...rest }: NearViewImageProps) {
  const ref = useRef<HTMLImageElement>(null);
  const [near, setNear] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    if (typeof IntersectionObserver === "undefined") {
      // Very old browser: load it, a task later (no synchronous setState in the effect body).
      const t = setTimeout(() => setNear(true), 0);
      return () => clearTimeout(t);
    }
    // A descendant of a skipped content-visibility section reports no intersection until the section renders;
    // the observer then reports again, so observing the image itself is enough.
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          io.disconnect();
        }
      },
      { rootMargin: NEAR_VIEW_MARGIN },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [near]);

  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        ref={ref}
        {...rest}
        src={near ? src : undefined}
        alt={alt}
        loading="lazy"
        data-near-view=""
        className={near ? className : `${className ?? ""} text-transparent`}
      />
      <noscript>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img {...rest} src={src} alt={alt} loading="lazy" className={className} />
      </noscript>
    </>
  );
}
