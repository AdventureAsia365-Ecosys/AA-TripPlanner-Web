// AA-1029 — the "from" price of a tour page. It comes only from AA-Booking (contract price.from.v1, the view
// booking.v_from_prices read by the browse Lambda): the suggested retail price per person for 2 travellers (root ADR
// 0019 b15). TripPlanner never computes a price; no price from Booking = nothing is shown.

export interface FromPrice {
  amount: string;
  currency: string;
  basis: "per_person";
  pax: number;
  season: string | null;
  valid_to: string | null;
}

/** "US$235" — whole units (the price is rounded up to 5 by Booking), en-US like the rest of the site. */
export function formatFromPrice(p: FromPrice | null | undefined): string | null {
  if (!p) return null;
  const n = Number(p.amount);
  if (!Number.isFinite(n) || n <= 0 || !/^[A-Z]{3}$/.test(p.currency)) return null;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: p.currency, maximumFractionDigits: 0 }).format(n);
  } catch {
    return null;
  }
}

/** The schema.org Offer of the page's JSON-LD, or undefined without a price. */
export function fromPriceOffer(p: FromPrice | null | undefined, url: string): Record<string, unknown> | undefined {
  if (!formatFromPrice(p) || !p) return undefined;
  return {
    "@type": "Offer",
    price: p.amount,
    priceCurrency: p.currency,
    url,
    ...(p.valid_to ? { priceValidUntil: p.valid_to } : {}),
    description: "Suggested price per person for 2 travellers; the advisor confirms the final price.",
  };
}
