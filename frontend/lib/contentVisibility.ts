// AA-995 (spec §6 step 5, R14) — inline style for a `.tp-cv` section (app/globals.css): its estimated block size
// before its first render. Below-fold sections only; the browser remembers the real size once it has rendered.
import type { CSSProperties } from "react";

export function cvSize(px: number): CSSProperties {
  return { ["--tp-cv-h" as string]: `${Math.max(0, Math.round(px))}px` };
}
