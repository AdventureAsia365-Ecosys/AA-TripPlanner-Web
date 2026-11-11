"use client";

// AA-1041 (R7 item 6): share the trip read-only, or open its print view. The link carries component ids only (no
// trip id, session or guest token; lib/shareTrip.ts), so whoever opens it sees the stops from the catalog and can
// change nothing. "Print view" opens the same page in a new tab, where the browser prints it or saves it as a PDF.
import { useState } from "react";
import { useTrip } from "@/lib/useTrip";
import { encodeShare, sharePath, shareUrl } from "@/lib/shareTrip";
import { plannerTenant } from "@/lib/plannerTenant";
import { Icon } from "@/ui/Icon";

export default function ShareTrip() {
  const { itinerary, customDays } = useTrip();
  const [copied, setCopied] = useState<"ok" | "failed" | null>(null);
  if (itinerary.length === 0) return null;
  const fragment = encodeShare(itinerary, customDays);
  const href = `${sharePath(plannerTenant())}#${fragment}`;

  const copy = async () => {
    const url = shareUrl(window.location.origin, plannerTenant(), fragment);
    try {
      if (navigator.share && window.matchMedia("(pointer: coarse)").matches) {
        await navigator.share({ title: "My trip", url });
        setCopied("ok");
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <div className="tp-no-print mb-3 rounded-xl border border-k-line bg-k-card p-2.5" data-testid="share-trip">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          data-testid="share-copy"
          className="aa-focus inline-flex items-center gap-1.5 rounded-full border border-k-accent-border px-3 py-1 text-xs font-semibold text-k-accent-deep transition hover:bg-k-accent-tint"
        >
          <Icon name="share" className="h-3.5 w-3.5" />
          Share link
        </button>
        <a
          href={href}
          target="_blank"
          rel="noopener"
          data-testid="share-open"
          className="aa-focus inline-flex items-center gap-1.5 rounded-full border border-k-line px-3 py-1 text-xs font-semibold text-k-ink transition hover:border-k-accent-border"
        >
          <Icon name="print" className="h-3.5 w-3.5" />
          Print view
        </a>
      </div>
      <p role="status" className="mt-1.5 text-[11px] text-k-muted">
        {copied === "ok"
          ? "Link copied. Anyone with it sees this trip and cannot change it."
          : copied === "failed"
            ? "We couldn't copy the link. Open the print view and copy its address instead."
            : "A read-only link: it shows the places of this trip, nothing about you."}
      </p>
    </div>
  );
}
