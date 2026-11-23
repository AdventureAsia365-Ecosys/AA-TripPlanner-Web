"use client";

// AA-1152 (S285 decision B2 option C, root ADR 0019 b5 as amended): the agency's OWN "from" price per traveller on its
// tour page, as its CRM published it (backend/shared/agency_prices.py, contract tripplanner.agency_prices.v1). Never
// AA's price; nothing when the agency published none, switched prices off or its list is out of date. Read on every
// view through the no-store route app/api/trip/agency-price/route.ts (the page is cached for up to 30 minutes, so the
// price never rides in it). The agency's quote is final.
import { useEffect, useState } from "react";
import { agencyPriceOf, priceText, type DayAddonPrice } from "@/lib/dayAddons";
import { tenantRequestHeaders } from "@/lib/plannerTenant";

export default function AgencyFromPrice({ slug, sourceId, agency }: { slug: string; sourceId: string; agency: string }) {
  const [price, setPrice] = useState<DayAddonPrice | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`/api/trip/agency-price?${new URLSearchParams({ source_id: sourceId })}`, {
      cache: "no-store",
      headers: tenantRequestHeaders(slug),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => live && setPrice(agencyPriceOf(body)))
      .catch(() => live && setPrice(null));
    return () => {
      live = false;
    };
  }, [slug, sourceId]);
  if (!price) return null;
  return (
    <p className="mt-3 text-small text-k-ink" data-testid="agency-from-price">
      From <span className="text-h3 font-semibold text-k-accent-deep">{priceText(price, "en-US")}</span> per person
      <span className="block text-caption text-k-muted">
        Indicative price; your final price is in {agency}&apos;s quote.
      </span>
    </p>
  );
}
