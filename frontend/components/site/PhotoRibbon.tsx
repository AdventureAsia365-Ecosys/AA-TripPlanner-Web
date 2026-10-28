// AA-995 (spec §3.6 + grafts) — the brand's sideways photo banner, done accessibly.
//   >= 8 unique photos: it moves (only with data-motion="ok", in view, not paused); 4–7: a still swipe row;
//   < 4: nothing is rendered (lib/photos.ts).
// Markup: a labelled section; the pause button comes before the track; copy A is the real list (its
// fill-rule repeats are aria-hidden), copy B is an aria-hidden duplicate, and every link in a repeat or in
// copy B has tabIndex -1, so nothing is announced or tabbed to twice. They are deliberately NOT `inert`:
// an inert node is skipped by hit-testing, so the duplicate tiles (half of every loop) would ignore clicks
// and hover while looking exactly like the live ones. All behaviour is CSS (app/globals.css .tp-ribbon*), the client island
// (RibbonControl) only toggles attributes. Keyboard focus on the viewport (link-less ribbons) or inside it
// switches it to the still, scrollable row (:has(:focus-visible)), so a focused tile is never off screen and
// arrow keys scroll the row (WCAG 2.4.11).
// The duration is computed here: --tp-ribbon-count tiles x the per-tile seconds of each breakpoint.
import Link from "next/link";
import type { CSSProperties } from "react";
import { cvSize } from "@/lib/contentVisibility";
import { ribbonMode, ribbonRepeats, uniqueRibbonItems, type RibbonItem } from "@/lib/photos";
import RibbonControl from "./RibbonControl";

function Tile({ item, hidden }: { item: RibbonItem & { src: string }; hidden: boolean }) {
  const body = (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={item.src} alt={hidden ? "" : item.alt} width={240} height={240} loading="lazy" decoding="async"
           sizes="(min-width: 768px) 240px, 160px" className="h-full w-full object-cover" />
      <span className="tp-ribbon-cap absolute bottom-2 left-2 max-w-[calc(100%-16px)] truncate rounded-pill bg-k-scrim px-2.5 py-1 text-caption text-white">
        {item.caption}
      </span>
    </>
  );
  return (
    <div className="tp-ribbon-tile relative overflow-hidden rounded-tile bg-k-hero-bg">
      {item.href ? (
        // AA-833: no prefetch on photo links.
        <Link href={item.href} prefetch={false} tabIndex={hidden ? -1 : undefined} className="block h-full w-full">
          {body}
        </Link>
      ) : (
        // A photo without a page (destination gallery): not a link, so nothing to tab to.
        <div className="h-full w-full">{body}</div>
      )}
    </div>
  );
}

export default function PhotoRibbon({
  id,
  title,
  label,
  items,
  cvHeight,
}: {
  id: string;
  title: string;
  // Accessible name of the photo list ("Photos from our trips").
  label: string;
  items: RibbonItem[];
  // Below the fold: skip rendering until near the viewport (globals.css .tp-cv); the estimated height in px.
  cvHeight?: number;
}) {
  const unique = uniqueRibbonItems(items);
  const mode = ribbonMode(unique.length);
  if (mode === "none") return null;
  const animate = mode === "animated";
  const reps = animate ? ribbonRepeats(unique.length) : 1;
  // Tiles without a link leave nothing to Tab to, so the scrollable still row itself takes focus (arrow keys scroll it).
  const linkless = unique.some((it) => !it.href);
  const style = { "--tp-ribbon-count": unique.length * reps, ...(cvHeight ? cvSize(cvHeight) : {}) } as CSSProperties;
  const copy = (copyIndex: number) =>
    Array.from({ length: reps }, (_, r) =>
      unique.map((it, i) => {
        const hidden = copyIndex > 0 || r > 0;
        return (
          <li key={`${r}-${i}`} {...(copyIndex === 0 && r > 0 ? { "data-repeat": "", "aria-hidden": true } : {})}>
            <Tile item={it} hidden={hidden} />
          </li>
        );
      }),
    );

  return (
    <section id={id} aria-labelledby={`${id}-title`} className={`tp-ribbon scroll-mt-header bg-k-bg py-section${cvHeight ? " tp-cv" : ""}`} data-animate={animate ? "" : undefined} style={style}>
      <div className="mx-auto flex max-w-page items-center justify-between gap-4 px-gutter">
        <h2 id={`${id}-title`} className="font-script text-script-sm text-k-ink">{title}</h2>
        {animate && <RibbonControl ribbonId={id} />}
      </div>
      <div className="tp-ribbon-viewport mt-6" {...(linkless ? { tabIndex: 0, role: "region", "aria-labelledby": `${id}-title` } : {})}>
        <div className="tp-ribbon-track">
          <ul aria-label={label} className="tp-ribbon-list">{copy(0)}</ul>
          {animate && (
            <ul aria-hidden="true" className="tp-ribbon-list tp-ribbon-copy">
              {copy(1)}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
