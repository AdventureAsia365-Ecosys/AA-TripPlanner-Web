// AA-1115 (root ADR 0019 d10 / d11) — /{slug}/privacy (on an agency's own domain: /privacy, rewritten here by proxy.ts):
// the DRAFT privacy notice of an agency's trip planner. The agency, not Adventure Asia, is responsible for the trips
// its travellers send it (the lead goes to the agency's own CRM, webhook or e-mail, lead.submitted.v1), so the page
// names the agency only and every factual sentence describes what the code does today (the guest cookie and the
// security cookie, the AI calls, the lead hand-off; app/privacy/page.tsx has the same facts for AA's planner). Open
// points stay [to be confirmed] for the agency. The AA-hosted demo tenant keeps AA's own page (/privacy).
import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import AgencyLegalPage, { LegalSection } from "@/components/AgencyLegalPage";
import { isAgencyPlanner } from "@/lib/leadForm";
import { PRIVACY_PATH, legalPaths } from "@/lib/legal";
import { isTenantSlug, resolveTenant } from "@/lib/tenantSite";
import { brandThemeCss } from "@/ui/core/brandTheme";

type Props = { params: Promise<{ tenant: string }> };

export const metadata: Metadata = {
  title: "Privacy notice (draft)",
  description: "Draft privacy notice of this trip planner, pending legal review.",
  robots: { index: false, follow: true },
};

export default async function TenantPrivacyPage({ params }: Props) {
  const { tenant } = await params;
  if (!isTenantSlug(tenant)) notFound();
  const site = await resolveTenant(tenant);
  if (!site) notFound();
  if (!isAgencyPlanner(site.slug)) redirect(PRIVACY_PATH);
  const a = site.name;
  const legal = legalPaths(site.slug);
  const brand = brandThemeCss(site.theme);
  return (
    <>
      {brand && <style data-testid="tenant-brand">{brand}</style>}
      <AgencyLegalPage
        site={site}
        title="Privacy notice"
        otherPage={{ href: legal.terms, label: "Terms of use" }}
        intro={
          <p>
            This notice explains what personal data the trip planner of {a} (this website) collects, why, who receives
            it and what you can ask {a} to do with it. It covers the trip planner only.
          </p>
        }
      >
        <LegalSection id="who" title="1. Who is responsible">
          <p>
            {a} offers this trip planner and is responsible for the trips you send it (the data controller). Its legal
            entity and registered address are [to be confirmed]. A technology provider hosts and runs the planner for
            {" "}{a} and handles your data only to do so [provider details to be confirmed]. For any privacy question or
            request, contact {a}.
          </p>
        </LegalSection>

        <LegalSection id="collect" title="2. What is collected">
          <p>You can explore tours and build a trip without giving your name or contact details.</p>
          <ul>
            <li>
              <strong>Your trip.</strong> The destinations, stops, activities and days you choose, and their order. It is
              saved under a random guest identifier, not under your name.
            </li>
            <li>
              <strong>What you type in the planner search and the planning chat.</strong> Used to find matching places
              and to suggest a route. Please do not type personal details there.
            </li>
            <li>
              <strong>Your request, only when you send your trip to {a}.</strong> Your name, your e-mail address, your
              phone number if you give it, who travels, and the start date, budget and message if you add them; whether
              you agreed that {a} may contact you about this trip and whether you asked for its travel ideas, with the
              date and time.
            </li>
            <li>
              <strong>Technical data.</strong> The hosting providers process your IP address, browser type and the pages
              you request in order to deliver the site and keep it secure.
            </li>
          </ul>
        </LegalSection>

        <LegalSection id="cookies" title="3. Cookies and browser storage">
          <p>Only what the planner needs to work. No advertising or analytics cookies.</p>
          <ul>
            <li>
              <strong>Guest cookie</strong> (set on the planner page only): a random token that proves a saved trip is
              yours. Scripts on the page cannot read it, and it lasts up to 400 days, renewed each time you use the
              planner. Only a one-way hash of it is stored.
            </li>
            <li>
              <strong>Security cookie</strong> (set when you open the planner): a short-lived token from the website
              firewall (Amazon Web Services) that shows the requests come from a real browser. It lasts about 4 hours and
              holds no personal details.
            </li>
            <li>
              <strong>Browser storage:</strong> the random ids of your current trip, so a reload restores it, and your
              light or dark display choice.
            </li>
          </ul>
        </LegalSection>

        <LegalSection id="use" title="4. How your data is used">
          <ul>
            <li>To build, save and show your trip.</li>
            <li>
              To deliver your request to {a}, only after you tick the consent box on the form. The planner keeps no copy
              of your contact details once the request is delivered.
            </li>
            <li>To send you travel ideas from {a}, only if you ticked that box.</li>
            <li>To keep the service secure and prevent abuse.</li>
          </ul>
          <p>The legal basis for each use under the laws that apply to you is [to be confirmed].</p>
        </LegalSection>

        <LegalSection id="ai" title="5. AI features">
          <p>
            The planner uses AI models to describe your trip day by day, to match your search text to places and to
            answer the planning chat. They are provided by Anthropic (Claude) and Amazon Web Services (Amazon Bedrock).
            They receive the contents of your
            trip (places, activities, days), your search text and your chat messages, never the contact details of your
            request. {a} confirms every detail of your trip before you book.
          </p>
        </LegalSection>

        <LegalSection id="share" title="6. Who receives your data">
          <ul>
            <li>
              <strong>{a}</strong>, which receives the request you send in its own system and answers it.
            </li>
            <li>
              <strong>Amazon Web Services</strong>: hosting and delivery of this website (Amazon CloudFront and its
              website firewall), the database and AI models (Amazon Bedrock).
            </li>
            <li>
              <strong>Anthropic</strong>: the AI model that writes the trip narration (Claude).
            </li>
            <li>
              <strong>Mapbox</strong>: map images and the interactive map. Your browser loads them from Mapbox, which
              receives your IP address and the map area you view.
            </li>
          </ul>
          <p>Your personal data is not sold.</p>
        </LegalSection>

        <LegalSection id="transfers" title="7. Where your data is stored">
          <p>
            The planner&apos;s database and servers are in the United States (Amazon Web Services). The safeguards for
            transfers outside your country are [to be confirmed].
          </p>
        </LegalSection>

        <LegalSection id="retention" title="8. How long it is kept">
          <p>Retention periods are [to be confirmed] by {a}.</p>
        </LegalSection>

        <LegalSection id="rights" title="9. Your rights">
          <p>
            You can ask {a} to show you the data it holds about you, correct it, delete it or stop contacting you, and
            you can withdraw your consent at any time. The time within which {a} answers, and your right to complain to a
            data protection authority, are [to be confirmed].
          </p>
        </LegalSection>
      </AgencyLegalPage>
    </>
  );
}
