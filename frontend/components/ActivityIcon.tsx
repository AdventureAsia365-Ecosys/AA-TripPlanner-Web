// Line-style activity icons — one per activity enum, matching AA's minimal
// white/gold line aesthetic (like adventure.asia's Interest icons). SVGs use
// `currentColor` for stroke so callers set colour via text color, and inherit
// size from the width/height prop. This is the single source of truth for
// activity iconography (replaces the emoji maps that were duplicated in
// lib/types.ts and DestinationPopup.tsx).

import type { SVGProps } from "react";

type IconProps = { size?: number } & SVGProps<SVGSVGElement>;

function base(size: number): SVGProps<SVGSVGElement> {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
}

// trekking — a boot / hiking
function Trekking({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M4 4h3v9" />
      <path d="M7 9c1.5 0 2 1.5 3.5 2l6 2c1.5.5 2.5 1.5 2.5 3H4" />
      <path d="M4 17h15" />
    </svg>
  );
}

// cultural_heritage — a classical building / landmark
function Culture({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M3 9l9-5 9 5" />
      <path d="M5 9v8M9.5 9v8M14.5 9v8M19 9v8" />
      <path d="M3 17h18M4 20h16" />
    </svg>
  );
}

// wildlife_nature — a paw print
function Wildlife({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <circle cx="7" cy="9" r="1.6" />
      <circle cx="12" cy="6.5" r="1.6" />
      <circle cx="17" cy="9" r="1.6" />
      <path d="M12 12c-2.8 0-5 2-5 4.2 0 1.6 1.4 2.3 2.7 1.8 1.5-.6 3.1-.6 4.6 0 1.3.5 2.7-.2 2.7-1.8C17 14 14.8 12 12 12Z" />
    </svg>
  );
}

// water_activities — waves
function Water({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M2 7c2 0 2 1.5 4 1.5S8 7 10 7s2 1.5 4 1.5S16 7 18 7s2 1.5 4 1.5" />
      <path d="M2 12c2 0 2 1.5 4 1.5S8 12 10 12s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5" />
      <path d="M2 17c2 0 2 1.5 4 1.5S8 17 10 17s2 1.5 4 1.5 2-1.5 4-1.5 2 1.5 4 1.5" />
    </svg>
  );
}

// culinary — fork & knife
function Culinary({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M6 3v7M9 3v7M6 10a1.5 1.5 0 0 0 3 0M7.5 10v11" />
      <path d="M16 3c-1.5 0-2.5 1.5-2.5 4s1 4 2.5 4V3Zm0 8v10" />
    </svg>
  );
}

// wellness_relaxation — lotus / spa
function Wellness({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M12 4c1.6 1.8 2.4 3.8 2.4 6-.8.4-1.6.6-2.4.6s-1.6-.2-2.4-.6C9.6 7.8 10.4 5.8 12 4Z" />
      <path d="M4.5 9.5c2.3.2 4.1 1 5.5 2.3-.4.8-1 1.5-1.8 2C6.6 12.9 5.2 11.5 4.5 9.5Z" />
      <path d="M19.5 9.5c-2.3.2-4.1 1-5.5 2.3.4.8 1 1.5 1.8 2 1.6-.9 3-2.3 3.7-4.3Z" />
      <path d="M4 15c2.5 2 5 3 8 3s5.5-1 8-3" />
    </svg>
  );
}

// adventure_sport — parachute / paragliding
function AdventureSport({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <path d="M2.5 11a9.5 7 0 0 1 19 0Z" />
      <path d="M8.7 11 12 15.5 15.3 11" />
      <path d="M12 4v7M12 15.5V19a2 2 0 0 0 2 2" />
    </svg>
  );
}

// local_immersion — hands / community
function LocalImmersion({ size = 16, ...p }: IconProps) {
  return (
    <svg {...base(size)} {...p} aria-hidden>
      <circle cx="12" cy="7" r="2.5" />
      <path d="M12 12c-3 0-5 1.8-5 4v2h10v-2c0-2.2-2-4-5-4Z" />
      <path d="M5 9.5 3 11M19 9.5 21 11" />
    </svg>
  );
}

const ICONS: Record<string, (p: IconProps) => JSX.Element> = {
  trekking: Trekking,
  cultural_heritage: Culture,
  wildlife_nature: Wildlife,
  water_activities: Water,
  culinary: Culinary,
  wellness_relaxation: Wellness,
  adventure_sport: AdventureSport,
  local_immersion: LocalImmersion,
};

/** Render the line icon for an activity enum. Falls back to a small dot for
 *  an unknown activity so layout never breaks. Colour follows text color. */
export default function ActivityIcon({
  activity,
  size = 16,
  ...rest
}: { activity: string; size?: number } & SVGProps<SVGSVGElement>) {
  const Cmp = ICONS[activity];
  if (!Cmp) {
    return (
      <svg {...base(size)} {...rest} aria-hidden>
        <circle cx="12" cy="12" r="2" />
      </svg>
    );
  }
  return <Cmp size={size} {...rest} />;
}
