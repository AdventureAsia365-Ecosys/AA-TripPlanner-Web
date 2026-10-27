"use client";
// AA-995 (spec §3.7, simplified per judge 3) — the featured tours row: user-driven, never autoplays (so it
// needs no pause control; the ribbon stays the only thing that moves by itself). A native scroll-snap row
// in its own overflow box (the page never scrolls sideways): swipe on touch, Tab moves card to card and
// the browser scrolls the focused card into view (scroll-padding), and from md two 44 px buttons page
// through it (aria-controls, aria-disabled at the ends). Smooth scroll only when data-motion="ok".
//
// The ends come from an IntersectionObserver on the first and last card (async callbacks: no setState in
// an effect body, React Compiler lint).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "@/ui";
import SectionTitle from "./SectionTitle";

export function TourCarouselButtons({
  controls,
  atStart,
  atEnd,
  onStep,
}: {
  controls: string;
  atStart: boolean;
  atEnd: boolean;
  onStep: (dir: -1 | 1) => void;
}) {
  const btn =
    "inline-flex h-11 w-11 items-center justify-center rounded-full border border-k-accent-border bg-k-card text-k-accent-deep transition-colors duration-fast ease-std hover:bg-k-accent-tint aria-disabled:cursor-default aria-disabled:opacity-40 aria-disabled:hover:bg-k-card";
  return (
    <div className="hidden items-center gap-2 md:flex">
      <button type="button" className={btn} aria-controls={controls} aria-disabled={atStart} aria-label="Previous tours"
              onClick={() => !atStart && onStep(-1)}>
        <Icon name="chevron-left" className="h-5 w-5" />
      </button>
      <button type="button" className={btn} aria-controls={controls} aria-disabled={atEnd} aria-label="Next tours"
              onClick={() => !atEnd && onStep(1)}>
        <Icon name="chevron-right" className="h-5 w-5" />
      </button>
    </div>
  );
}

// The title row is rendered here (not passed in as a render function: a Server Component cannot hand a
// function to a Client Component), so the prev / next buttons sit in it next to `action` (spec §4.1 row 5).
export default function TourCarousel({
  id,
  label,
  titleId,
  title,
  lead,
  action,
  children,
}: {
  id: string;
  label: string;
  titleId: string;
  title: string;
  lead?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  const list = useRef<HTMLUListElement>(null);
  const [ends, setEnds] = useState({ start: true, end: false });

  useEffect(() => {
    const el = list.current;
    const first = el?.firstElementChild;
    const last = el?.lastElementChild;
    if (!el || !first || !last || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const full = e.intersectionRatio >= 0.95;
          if (e.target === first) setEnds((s) => ({ ...s, start: full }));
          if (e.target === last) setEnds((s) => ({ ...s, end: full }));
        }
      },
      { root: el, threshold: [0, 0.95, 1] },
    );
    io.observe(first);
    io.observe(last);
    return () => io.disconnect();
  }, []);

  const step = (dir: -1 | 1) => {
    const el = list.current;
    if (!el) return;
    const smooth = document.documentElement.getAttribute("data-motion") === "ok";
    el.scrollBy({ left: dir * el.clientWidth * 0.9, behavior: smooth ? "smooth" : "auto" });
  };

  return (
    <>
      <div data-reveal="">
        <SectionTitle
          id={titleId}
          title={title}
          lead={lead}
          action={
            <>
              {action}
              <TourCarouselButtons controls={id} atStart={ends.start} atEnd={ends.end} onStep={step} />
            </>
          }
        />
      </div>
      <ul
        id={id}
        ref={list}
        aria-label={label}
        className="tp-snap-row -mx-gutter mt-8 grid auto-cols-[calc(100%-56px)] grid-flow-col gap-3 overflow-x-auto px-gutter pb-4 pt-2 md:auto-cols-[calc((100%-24px)/2.15)] md:gap-6 lg:auto-cols-[calc((100%-48px)/3.25)]"
      >
        {children}
      </ul>
    </>
  );
}
