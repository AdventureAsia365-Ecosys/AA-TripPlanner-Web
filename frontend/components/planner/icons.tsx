// Line icons for the planner (AA-674). SVG, not emoji: emoji fall back to empty boxes on systems
// without an emoji font, which the production review showed.

import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm12-10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM6 15V9a4 4 0 0 1 4-4h2m6 6v4a4 4 0 0 1-4 4h-2",
  pin: "M12 21s-7-6.1-7-11a7 7 0 1 1 14 0c0 4.9-7 11-7 11Zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  calendar: "M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z",
  heart: "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z",
  chevron: "m6 9 6 6 6-6",
  back: "M15 18l-6-6 6-6",
  sparkle: "M12 3l1.8 4.9L19 9.5l-5.2 1.6L12 16l-1.8-4.9L5 9.5l5.2-1.6L12 3Zm7 11 .8 2.2L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-.8L19 14Z",
  check: "m5 12 4.5 4.5L19 7",
  plus: "M12 5v14M5 12h14",
  close: "M6 6l12 12M18 6 6 18",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z",
  plane: "M10.5 13.5 3 11l1-2 8 1 4-5.5a1.8 1.8 0 0 1 2.5 2.5L13 11l1 8-2 1-2.5-7.5Z",
  car: "M5 16V11l2-4h10l2 4v5M5 16h14M5 16v2m14-2v2M7.5 13.5h.01M16.5 13.5h.01",
  tour: "M4 20V6l8-3 8 3v14l-8-3-8 3Zm8-17v14",
  bed: "M3 18v-7m0 3h18v4m0-4v-2a3 3 0 0 0-3-3h-7v5M7 11.5h.01",
};

export function Icon({ name, ...rest }: { name: keyof typeof PATHS | string } & SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
         strokeLinejoin="round" aria-hidden {...rest}>
      <path d={PATHS[name] ?? PATHS.pin} />
    </svg>
  );
}

// One colour per trip day, shared by the day tabs and the map (like Trip.com's day colours).
export const DAY_COLORS = ["#DB9628", "#2F7D6D", "#3B6FB6", "#B4478C", "#7A5AC7", "#C8553D", "#4E8F2F", "#1F2A37"];
export function dayColor(day: number): string {
  return DAY_COLORS[(Math.max(1, day) - 1) % DAY_COLORS.length];
}
