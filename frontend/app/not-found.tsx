// AA-812 — the root 404: Adventure Asia's own (an unknown slug, an unmatched path, a missing tour or destination of
// AA's catalogue). AA-1115 follow-up: Next renders this file into the RSC payload of every page, an agency's /{slug}
// pages and its own domain included, even when nothing is missing. So it renders only a client boundary
// (components/site/AaNotFound.tsx) and a flag: no brand word may sit in its server output (lib/agencyBrand.test.ts
// walks this file up to its client boundaries; scripts/check-agency-render.mjs greps the built pages). An agency's
// own 404 is components/TenantNotFound.tsx, from the not-found files under app/[tenant].
import AaNotFound from "@/components/site/AaNotFound";
import { isLiveSite } from "@/lib/siteMode";

export default function NotFound() {
  return <AaNotFound preview={!isLiveSite()} />;
}
