// AA-1115 (root ADR 0019 d10 / d11) — /{slug}/terms (on an agency's own domain: /terms, rewritten here by proxy.ts):
// the DRAFT terms of use of an agency's trip planner, naming the agency only. The "Use of AI" section (id "ai", the
// planner's AI notices link /{slug}/terms#ai) says what AI writes and links the Anthropic Usage Policy, as AA's terms do
// (root docs/legal/tenant-ai-schedule.md §2 (c): travellers are end users of a product that integrates Claude). The
// AA-hosted demo tenant keeps AA's own page (/terms).
import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import AgencyLegalPage, { LegalSection } from "@/components/AgencyLegalPage";
import { isAgencyPlanner } from "@/lib/leadForm";
import { ANTHROPIC_USAGE_POLICY_URL, TERMS_PATH, legalPaths } from "@/lib/legal";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";
import { plannerBrandCss } from "@/lib/tenantBrand";

type Props = { params: Promise<{ tenant: string }> };

export const metadata: Metadata = {
  title: "Terms of use (draft)",
  description: "Draft terms of use of this trip planner, pending legal review.",
  robots: { index: false, follow: true },
};

export default async function TenantTermsPage({ params }: Props) {
  const { tenant } = await params;
  if (!isTenantSlug(tenant)) notFound();
  const site = await resolveTenant(tenant);
  if (!site) notFound();
  if (!isAgencyPlanner(site.slug)) redirect(TERMS_PATH);
  const a = site.name;
  const legal = legalPaths(site.slug);
  const brand = plannerBrandCss(site.theme);
  return (
    <>
      {brand && <style data-testid="tenant-brand">{brand}</style>}
      <AgencyLegalPage
        site={site}
        title="Terms of use"
        otherPage={{ href: legal.privacy, label: "Privacy notice" }}
        intro={
          <p>
            These terms apply when you use the trip planner of {a} (this website). By using it you accept them. If you do
            not agree, please do not use the site.
          </p>
        }
      >
        <LegalSection id="service" title="1. What the trip planner is">
          <p>
            A free tool of {a} for exploring its tours and shaping a trip idea day by day. The operating legal entity of
            {" "}{a} is [to be confirmed].
          </p>
        </LegalSection>

        <LegalSection id="not-a-booking" title="2. A trip plan is not a booking">
          <ul>
            <li>
              A trip you build or send to {a} is a request. Nothing is reserved or paid, and no contract for travel
              services is made through this site.
            </li>
            <li>
              {a} reviews your trip and contacts you to confirm logistics, availability and price. A booking exists only
              when you agree to it with {a} under its own booking conditions.
            </li>
          </ul>
        </LegalSection>

        <LegalSection id="ai" title="3. Use of AI">
          <ul>
            <li>
              AI helps write some of the text on this site: tour descriptions and their day-by-day itineraries, the
              day-by-day narration of a trip you build and the answers of the planning chat. The place search and the
              &ldquo;You might also like&rdquo; suggestions use AI to match your words and the places you picked.
              AI-written text is labelled as such.
            </li>
            <li>
              The trip narration is written by Anthropic&apos;s Claude. When you use the site&apos;s AI features,
              the{" "}
              <a href={ANTHROPIC_USAGE_POLICY_URL} target="_blank" rel="noopener noreferrer">
                Anthropic Usage Policy
              </a>{" "}
              applies to you too: for example, do not use them to create fake reviews or misleading content.
            </li>
            <li>
              AI can make mistakes, so this text can contain errors. It is there for information, to help you explore
              and shape a trip idea. Do not rely on it for travel, visa, health or safety decisions.
            </li>
            <li>
              Prices, itineraries and visa conditions are confirmed in your official quote from {a}. What binds you and
              {" "}{a} is that confirmed quote and the booking conditions you accept, not the text on this site.
            </li>
          </ul>
        </LegalSection>

        <LegalSection id="your-use" title="4. Using the site">
          <ul>
            <li>Give accurate contact details when you send a trip, and only your own.</li>
            <li>Use the site only for planning your own travel, and in line with the law.</li>
            <li>
              Do not try to access other people&apos;s trips, overload or disrupt the service, or copy its content in
              bulk with automated tools.
            </li>
          </ul>
        </LegalSection>

        <LegalSection id="content" title="5. Maps">
          <p>
            Maps are provided by Mapbox and OpenStreetMap contributors under their own terms. Place locations are
            geocoded with Geoapify (geoapify.com) from OpenStreetMap data (© OpenStreetMap contributors, ODbL).
          </p>
        </LegalSection>

        <LegalSection id="law" title="6. Liability and governing law">
          <p>Limits of liability, consumer rights, the governing law and the competent courts are [to be confirmed].</p>
        </LegalSection>
      </AgencyLegalPage>
    </>
  );
}
