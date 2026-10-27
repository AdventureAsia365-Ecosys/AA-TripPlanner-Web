"use client";
// AA-995 (spec M6) — one shared IntersectionObserver for the scroll reveals. The server renders
// data-reveal="" (fully visible). Only when data-motion="ok": an element wholly below the fold at first
// sight becomes "pending" (hidden by CSS) and "in" (fade + 16 px rise, once) when 15 % of it enters.
// Elements already in view are never hidden, and without JS / under reduced motion nothing is hidden.
import { useEffect } from "react";

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
          } else if (el.dataset.reveal === "" && e.boundingClientRect.top >= window.innerHeight) {
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
