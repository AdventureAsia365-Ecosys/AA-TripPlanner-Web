// AA-1115 — any other path under /{slug} (/{slug}/destinations, /{slug}/plan/x, /{slug}/tours/a/b...): without this
// catch-all Next would answer it with the root app/not-found.tsx, Adventure Asia's 404, on an agency's site. Static
// siblings (plan, privacy, terms, tours/[tour]) win over it; every request here is a 404 with the agency 404 page
// (./not-found.tsx). The bare /{slug} stays app/[tenant]/page.tsx; a slug that is no served tenant never gets here
// (app/[tenant]/layout.tsx sends it to the root 404, as before).
import { notFound } from "next/navigation";

export default function TenantUnknownPage(): never {
  notFound();
}
