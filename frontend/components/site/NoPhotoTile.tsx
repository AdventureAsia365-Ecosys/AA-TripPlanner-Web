// AA-995 (spec §3.5 + immersive graft 7) — the designed placeholder wherever a photo is missing or would
// repeat: a navy (or gold-tint) surface with the logo's mountain lines and the place name in Satisfy.
// Never the bare camera icon on customer pages. Two no-photo tiles side by side alternate tones.
export type NoPhotoTone = "band" | "tint";

const TONES: Record<NoPhotoTone, { box: string; name: string; caption: string; motif: string }> = {
  band: { box: "bg-k-band", name: "text-k-band-accent", caption: "text-k-on-band-muted", motif: "text-k-band-accent" },
  tint: { box: "bg-k-accent-tint", name: "text-k-accent-deep", caption: "text-k-muted", motif: "text-k-accent" },
};

// The logo's mountain lines as a decorative SVG (no-photo tiles, the no-photo page hero, the status pages). Position,
// size and colour come from the caller; it is absolutely placed and never takes clicks.
export function MountainMotif({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 120 60"
      aria-hidden
      focusable="false"
      className={`pointer-events-none absolute opacity-20 ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinejoin="round"
      strokeLinecap="round"
    >
      <path d="M2 58 32 20l14 16 20-30 26 34 12-12 14 30" />
      <path d="M22 58 42 34l10 10 16-20 20 26" />
    </svg>
  );
}

export function noPhotoTone(index: number): NoPhotoTone {
  return index % 2 === 0 ? "band" : "tint";
}

export default function NoPhotoTile({
  name,
  caption,
  tone = "band",
  decorative = false,
  className = "",
}: {
  name: string;
  caption?: string | null;
  tone?: NoPhotoTone;
  // Inside a link that already names the place: hide the tile's text from assistive tech.
  decorative?: boolean;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <div
      className={`relative flex flex-col justify-end overflow-hidden p-5 ${t.box} ${className}`}
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": caption ? `${name}, ${caption}` : name })}
    >
      {/* The logo's mountain lines, 18 % opacity, bottom-right, 60 % of the tile width. */}
      <MountainMotif className={`-bottom-1 right-0 w-3/5 ${t.motif}`} />
      <p className={`relative font-script text-script-sm ${t.name}`}>{name}</p>
      {caption && <p className={`relative mt-1 text-caption ${t.caption}`}>{caption}</p>}
    </div>
  );
}
