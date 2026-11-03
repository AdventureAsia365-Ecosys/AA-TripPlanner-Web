// AA-886 — /terms: DRAFT terms of use of the Trip Planner. Product facts only (a free planning tool;
// a sent trip is an enquiry, not a booking; AA-1033 §3 "Use of AI": what AI writes, that it can be wrong, that the
// confirmed quote and booking conditions bind); liability, governing law and
// booking conditions are left [to be confirmed] for counsel. Not indexed while it is a draft.
// AA-820 (S266, Draft v0.2): §3 says which model writes the AI text (Claude through Amazon Bedrock, AI Use Register
// row tp_compose) and links the Anthropic Usage Policy, which reaches travellers as end users of a product built on it.
import type { Metadata } from "next";
import Link from "next/link";
import LegalPage, { LegalSection } from "@/components/site/LegalPage";
import { ANTHROPIC_USAGE_POLICY_URL, LEGAL_CONTACT_EMAIL, PRIVACY_PATH } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Terms of use (draft)",
  description: "Draft terms of use of the Adventure Asia Trip Planner, pending legal review.",
  alternates: { canonical: "/terms" },
  robots: { index: false, follow: true },
};

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of use"
      otherPage={{ href: PRIVACY_PATH, label: "Privacy notice" }}
      intro={
        <p>
          These terms apply when you use the Adventure Asia Trip Planner (this website). By using it you accept them. If
          you do not agree, please do not use the site.
        </p>
      }
    >
      <LegalSection id="service" title="1. What the Trip Planner is">
        <p>
          The Trip Planner is a free tool for exploring Adventure Asia tours and shaping a trip idea day by day. It is
          run by Adventure Asia; the operating legal entity is [to be confirmed].
        </p>
      </LegalSection>

      <LegalSection id="not-a-booking" title="2. A trip plan is not a booking">
        <ul>
          <li>
            A trip you build or send to an advisor is an enquiry. Nothing is reserved or paid, and no contract for travel
            services is made through this site.
          </li>
          <li>
            An Adventure Asia advisor reviews your trip and contacts you to confirm logistics, availability and price. A
            booking exists only when you agree to it with Adventure Asia under separate booking conditions [booking
            conditions to be confirmed].
          </li>
          <li>Prices, availability and itineraries shown on the site are indicative and can change.</li>
        </ul>
      </LegalSection>

      <LegalSection id="ai" title="3. Use of AI">
        <ul>
          <li>
            AI helps write some of the text on this site: tour descriptions and their day-by-day itineraries (written by
            AI from Adventure Asia&apos;s own tour notes and checked by automated quality checks), and the day-by-day
            narration of a trip you build. The place search and the &ldquo;You might also like&rdquo; suggestions use AI
            to match your words and the places you picked. AI-written text is labelled as such.
          </li>
          <li>
            The trip narration is written by Anthropic&apos;s Claude, through Amazon Bedrock (Amazon Web Services), on
            Adventure Asia&apos;s own account. When you use the site&apos;s AI features, the{" "}
            <a
              href={ANTHROPIC_USAGE_POLICY_URL}
              className="font-semibold text-k-accent-deep hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              Anthropic Usage Policy
            </a>{" "}
            applies to you too: for example, do not use them to create fake reviews or misleading content.
          </li>
          <li>
            AI can make mistakes, so this text can contain errors. It is there for information, to help you explore and
            shape a trip idea. Do not rely on it for travel, visa, health or safety decisions.
          </li>
          <li>
            Prices, itineraries and visa conditions are confirmed in your official quote. What binds you and Adventure
            Asia is that confirmed quote and the booking conditions you accept, not the text on this site; your advisor
            confirms the details before anything is booked.
          </li>
        </ul>
      </LegalSection>

      <LegalSection id="your-use" title="4. Using the site">
        <ul>
          <li>Give accurate contact details when you send a trip, and only your own.</li>
          <li>Use the site only for planning your own travel, and in line with the law.</li>
          <li>
            Do not try to access other people&apos;s trips, overload or disrupt the service, or copy its content in bulk
            with automated tools.
          </li>
        </ul>
        <p>We may limit or block access that breaks these rules.</p>
      </LegalSection>

      <LegalSection id="content" title="5. Content and maps">
        <p>
          The tours, texts, photos and design of the site belong to Adventure Asia or its licensors. Maps are provided by
          Mapbox and OpenStreetMap contributors under their own terms. Place locations are geocoded with Geoapify
          (geoapify.com) from OpenStreetMap data (© OpenStreetMap contributors, ODbL); a place we could only place
          approximately is shown as an area, not as an exact point. You may use the site for your personal, non-commercial
          trip planning.
        </p>
      </LegalSection>

      <LegalSection id="availability" title="6. Availability and changes">
        <p>
          The site is under active development. Features can change, and the site may be unavailable at times. We may
          update these terms; the date at the top of the page shows the current version.
        </p>
      </LegalSection>

      <LegalSection id="liability" title="7. Liability">
        <p>Limits of liability, warranties and consumer rights under the laws that apply to you are [to be confirmed].</p>
      </LegalSection>

      <LegalSection id="law" title="8. Governing law">
        <p>The governing law and competent courts are [to be confirmed].</p>
      </LegalSection>

      <LegalSection id="contact" title="9. Contact">
        <p>
          Questions about these terms: <a href={`mailto:${LEGAL_CONTACT_EMAIL}`}>{LEGAL_CONTACT_EMAIL}</a>. How we handle
          personal data is described in our <Link href={PRIVACY_PATH}>Privacy notice</Link>.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
