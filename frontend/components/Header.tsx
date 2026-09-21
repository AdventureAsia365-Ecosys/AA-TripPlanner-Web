"use client";

// Top brand bar — Adventure Asia wordmark + tagline. Kept slim so the map
// dominates the viewport (map-first product).
export default function Header() {
  return (
    <header
      className="z-30 flex items-center justify-between border-b border-aa-line bg-white px-5"
      style={{ height: "56px", flexShrink: 0 }}
    >
      <div className="flex items-center gap-3">
        {/* Official Adventure Asia logo */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/logo.png"
          alt="Adventure Asia"
          className="h-9 w-auto"
        />
        <div className="hidden leading-tight sm:block">
          <span className="text-[11px] font-medium uppercase tracking-[0.18em] text-aa-gold">
            Trip Planner
          </span>
          <p className="hidden text-[11px] text-aa-muted md:block">
            Discreet executive adventures, crafted day by day
          </p>
        </div>
      </div>

      <nav className="flex items-center gap-2 text-sm">
        <span className="hidden text-aa-muted md:inline">
          Pin experiences · build your trip · hand off to an advisor
        </span>
      </nav>
    </header>
  );
}
