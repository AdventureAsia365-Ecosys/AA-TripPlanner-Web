"use client";

// AA-1041 (R7 item 1): the photo of a stop or a day: the place's CIS cover photo (GET /browse/stops), lazy, in a
// fixed box so nothing moves when it lands. No photo, or one that fails to load, shows the stop's icon on the brand
// tint instead (graceful: CIS photo coverage is still growing, AA-997). Decorative: the stop's name is next to it.
import { useState } from "react";
import { PlaceIcon } from "./ActivityIcon";
import type { PlaceKind } from "@/lib/placeIcon";

export default function StopPhoto({
  src,
  kind,
  size = "md",
  className = "",
}: {
  src: string | null;
  kind: PlaceKind;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  // The URL that failed (tagged, so a new photo for the box is tried again without an effect).
  const [failed, setFailed] = useState<string | null>(null);
  const box = size === "lg" ? "h-28 w-full" : size === "md" ? "h-14 w-14" : "h-9 w-9";
  const icon = size === "sm" ? 16 : 22;
  if (!src || failed === src) {
    return (
      <span
        data-testid="stop-photo-fallback"
        className={`${box} inline-flex shrink-0 items-center justify-center rounded-tile bg-k-accent-tint text-k-accent-deep ${className}`}
      >
        <PlaceIcon kind={kind} size={icon} />
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a CIS photo URL that redirects to the image CDN
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      data-testid="stop-photo"
      onError={() => setFailed(src)}
      className={`${box} shrink-0 rounded-tile bg-k-line object-cover ${className}`}
    />
  );
}
