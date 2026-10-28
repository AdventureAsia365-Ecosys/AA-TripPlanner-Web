"use client";
// AA-995 (spec M6) — one shared IntersectionObserver for the scroll reveals. The server renders
// data-reveal="" (fully visible). Only when data-motion="ok": an element wholly below the fold at first
// sight becomes "pending" (hidden by CSS) and "in" (fade + 16 px rise, once) when 15 % of it enters.
// Elements already in view are never hidden, and without JS / under reduced motion nothing is hidden.
import { useEffect } from "react";

// Inside a section the browser skips (`.tp-cv`, content-visibility: auto) the observer reports a descendant at
// top 0 / height 0, which would never count as "below the fold" and so never animate. Measure the skipped
// section instead: its box keeps its real (estimated or remembered) position.
function revealTop(el: HTMLElement, e: IntersectionObserverEntry): number {
  const box = el.closest<HTMLElement>(".tp-cv");
  return box ? box.getBoundingClientRect().top : e.boundingClientRect.top;
}

export default function RevealObserver() {
  useEffect(() => {
    if (document.documentElement.getAttribute("data-motion") !== "ok" || !("IntersectionObserver" in window)) return;
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const el = e.target as HTMLElement;
          if (e.isIntersecting) {
            if (el.dataset.reveal === "pending") el.dataset.reveal = "in";
            io.unobserve(el);
          } else if (el.dataset.reveal === "" && revealTop(el, e) >= window.innerHeight) {
            // Wholly below the fold: safe to hide until it enters (never hide what is on screen).
            el.dataset.reveal = "pending";
          }
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -10% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
  return null;
}
