"use client";
// AA-995 (spec M4) — turns the transparent header solid once the hero's bottom edge has passed under it.
// One IntersectionObserver on the 1 px sentinel at the hero's bottom ([data-hero-end]); no scroll listener.
// It only toggles an attribute (no React state); the colour change is CSS (app/globals.css .tp-header).
import { useEffect } from "react";

export default function HeaderScrollState() {
  useEffect(() => {
    const header = document.querySelector<HTMLElement>("header[data-over-hero]");
    const end = document.querySelector("[data-hero-end]");
    if (!header) return;
    if (!end || !("IntersectionObserver" in window)) {
      header.setAttribute("data-solid", "");
      return;
    }
    const io = new IntersectionObserver(
      ([e]) => {
        // Solid once the sentinel is above the header's bottom edge (scrolled past), not when it is
        // still below the fold on a short screen.
        header.toggleAttribute("data-solid", !e.isIntersecting && e.boundingClientRect.top < header.offsetHeight);
      },
      { rootMargin: `-${header.offsetHeight}px 0px 0px 0px` },
    );
    io.observe(end);
    return () => io.disconnect();
  }, []);
  return null;
}
