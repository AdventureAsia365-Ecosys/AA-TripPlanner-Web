// AA-1152 (S285 decision B2 option C, root ADR 0019 b5 as amended): the agency's OWN "from" price per traveller on its
// tour page, as its CRM published it (backend/shared/agency_prices.py, contract tripplanner.agency_prices.v1). Never
// AA's price; nothing when the agency published none or switched prices off. The agency's quote is final.
import { priceText, type DayAddonPrice } from "@/lib/dayAddons";

export default function AgencyFromPrice({ price, agency }: { price?: DayAddonPrice | null; agency: string }) {
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
