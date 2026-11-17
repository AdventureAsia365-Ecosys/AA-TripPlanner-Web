// AA-1115 — the 404 of an agency's tour page (a tour the agency did not make its own, or one AA no longer serves): the
// brand-free agency 404 (components/TenantNotFound.tsx), never Adventure Asia's.
import TenantNotFound from "@/components/TenantNotFound";

export default function TenantTourNotFound() {
  return (
    <TenantNotFound title="We couldn't find that tour">
      It may no longer be offered, or the link is not complete.
    </TenantNotFound>
  );
}
