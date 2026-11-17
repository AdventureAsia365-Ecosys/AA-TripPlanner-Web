// AA-1115 — a missing page under /{slug} (an agency's site, or its own domain rewritten onto it): the brand-free agency
// 404 (components/TenantNotFound.tsx), never Adventure Asia's app/not-found.tsx.
import TenantNotFound from "@/components/TenantNotFound";

export default function TenantPageNotFound() {
  return (
    <TenantNotFound title="We couldn't find that page">
      The link may be incomplete, or the page has moved.
    </TenantNotFound>
  );
}
