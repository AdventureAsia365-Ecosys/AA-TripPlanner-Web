"use client";
// AA-995 (S256, mobile LCP) — the LCP image of a page (home hero picture, destination hero, tour gallery photo).
//
// Why a client component: React Flight turns every eager `<img>` (and every `<link rel="preload">`) a Server
// Component renders into an image preload hint in the RSC payload. When another page prefetches this route (a
// next/link in view), the router applies those hints, so the visitor downloads THIS page's hero at high priority
// next to the LCP image of the page they are on. Measured S256 (local build, Lighthouse mobile): the tour page
// preloaded the Sri Lanka destination hero (sizes 100vw → the 1600 w file, 142 KB) through its "Sri Lanka" links.
// A Client Component is not rendered by Flight (its props travel as data), so no hint is emitted; it is still
// server-rendered to HTML (React DOM SSR), where it keeps fetchpriority=high and the HTML image preload.
//
// It renders exactly the element it is given: no state, no effects, no change after hydration.
import type { ImgHTMLAttributes } from "react";

export type LcpImageProps = ImgHTMLAttributes<HTMLImageElement> & { alt: string; src: string };

export default function LcpImage(props: LcpImageProps) {
  // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text -- alt is required by the props type
  return <img {...props} />;
}
