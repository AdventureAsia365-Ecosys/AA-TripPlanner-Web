// AA-886 — /privacy: DRAFT privacy notice of the Trip Planner. Every factual sentence describes what
// the code does today (sources in docs/implementation-notes/AA-886-s245.md); the open legal points are
// marked [to be confirmed] for counsel. Not indexed while it is a draft.
import type { Metadata } from "next";
import Link from "next/link";
import LegalPage, { LegalSection } from "@/components/site/LegalPage";
import { COMPANY_PRIVACY_URL, LEGAL_CONTACT_EMAIL, TERMS_PATH } from "@/lib/legal";

export const metadata: Metadata = {
  title: "Privacy notice (draft)",
  description: "Draft privacy notice of the Adventure Asia Trip Planner, pending legal review.",
  alternates: { canonical: "/privacy" },
  robots: { index: false, follow: true },
};

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy notice"
      otherPage={{ href: TERMS_PATH, label: "Terms of use" }}
      intro={
        <p>
          This notice explains what personal data the Adventure Asia Trip Planner (this website) collects, why, who
          receives it and what you can ask us to do with it. It covers the Trip Planner only. Adventure Asia&apos;s
          company-wide policy is at <a href={COMPANY_PRIVACY_URL} className="font-semibold text-k-accent-deep hover:underline" rel="noopener">adventure.asia/privacy-policy</a>.
        </p>
      }
    >
      <LegalSection id="who" title="1. Who we are">
        <p>
          The Trip Planner is run by Adventure Asia. The legal entity responsible for your data (the data controller)
          and its registered address are [to be confirmed]. For any privacy question or request, write to{" "}
          <a href={`mailto:${LEGAL_CONTACT_EMAIL}`}>{LEGAL_CONTACT_EMAIL}</a>.
        </p>
      </LegalSection>

      <LegalSection id="collect" title="2. What we collect">
        <p>You can browse tours and build a trip without giving us your name or contact details.</p>
        <ul>
          <li>
            <strong>Your trip.</strong> The destinations, stops, activities, number of days and travellers you choose,
            and the order of your days. It is saved under a random guest identifier, not under your name.
          </li>
          <li>
            <strong>What you type in the planner search.</strong> Used to find matching places and activities.
            Please do not type personal details into the search box.
          </li>
          <li>
            <strong>Your contact details, only when you send a trip to an advisor.</strong> Your full name, your phone
            number and/or email address, and whether you agreed that an advisor may contact you, with the date and time
            you agreed.
          </li>
          <li>
            <strong>Your account, only if you sign in.</strong> Your email address, which you prove with a code we email
            you (there is no password), is kept by our sign-in service (Amazon Cognito). The planner itself stores only a
            random account identifier and the time you last signed in, and links the trips you plan to it so you can
            open them on any device. When you send a trip while signed in, the advisor receives your signed-in email,
            marked as confirmed.
          </li>
          <li>
            <strong>Technical data.</strong> Our hosting providers process your IP address, browser type and the pages
            you request in order to deliver the site and keep it secure.
          </li>
        </ul>
      </LegalSection>

      <LegalSection id="cookies" title="3. Cookies and browser storage">
        <p>We use only what the planner needs to work. We do not use advertising or analytics cookies.</p>
        <ul>
          <li>
            <strong>Guest cookie</strong> (set on the planner page only): a random token that proves a saved trip is
            yours. It cannot be read by scripts on the page, and it lasts up to 400 days, renewed each time you use the
            planner. We store only a one-way hash of it.
          </li>
          <li>
            <strong>Sign-in cookies</strong> (only if you sign in): your encrypted sign-in session, so you stay signed in
            for up to 30 days, and, for 10 minutes while you enter the emailed code, the step you are on. Scripts on the
            page cannot read them. Signing out removes them, together with the guest cookie of that browser.
          </li>
          <li>
            <strong>Browser storage:</strong> the random ids of your current trip, so a reload restores it, and your
            light or dark display choice.
          </li>
        </ul>
        <p>Clearing your browser data removes them; you then start a new trip.</p>
      </LegalSection>

      <LegalSection id="use" title="4. How we use your data">
        <ul>
          <li>To build, save and show your trip (providing the service you asked for).</li>
          <li>
            To pass your trip and contact details to an Adventure Asia advisor who contacts you about that trip. We do
            this only after you tick the consent box on the form, and we record that consent with its date, time and
            source.
          </li>
          <li>
            Your consent covers contact about the trip you sent. We do not use it for newsletters or other marketing.
          </li>
          <li>To keep the service secure and prevent abuse.</li>
        </ul>
        <p>The legal basis for each use under the laws that apply to you is [to be confirmed].</p>
      </LegalSection>

      <LegalSection id="ai" title="5. AI features">
        <p>
          The planner uses AI models to write the day-by-day description of your trip and to match your search text to
          places. These models run on Amazon Bedrock (Amazon Web Services). We send them the contents of your trip
          (places, activities, days) and your search text, never your name or contact details. Every itinerary is
          reviewed by an Adventure Asia advisor before you book.
        </p>
      </LegalSection>

      <LegalSection id="share" title="6. Who receives your data">
        <ul>
          <li>
            <strong>Adventure Asia advisors</strong>, through Adventure Asia&apos;s booking system, which receives the
            trips you send and keeps the customer record. Advisors are notified of a new inquiry by email.
          </li>
          <li>
            <strong>Amazon Web Services</strong> — hosting, database, the AI models above (Amazon Bedrock), sign-in
            (Amazon Cognito) and the emails: advisor notifications and sign-in codes (Amazon SES).
          </li>
          <li>
            <strong>Vercel</strong> — hosts and delivers this website.
          </li>
          <li>
            <strong>Mapbox</strong> — map images and the interactive map. Your browser loads them from Mapbox, which
            receives your IP address and the map area you view.
          </li>
        </ul>
        <p>We do not sell your personal data. The full list of service providers will be kept in a register [to be confirmed].</p>
      </LegalSection>

      <LegalSection id="transfers" title="7. Where your data is stored">
        <p>
          Our database and servers are in the United States (Amazon Web Services). Vercel and Mapbox serve the site and
          maps from locations close to you. The safeguards for transfers outside your country are [to be confirmed].
        </p>
      </LegalSection>

      <LegalSection id="retention" title="8. How long we keep it">
        <p>
          Retention periods are being set and are [to be confirmed]. The current plan: the copy of your contact details
          inside the message that sends your trip to the booking system is removed after 90 days, and the customer record
          in the booking system is kept while your enquiry or booking is active and then for a period [to be confirmed].
          Trips that were never sent [retention to be confirmed]. A sign-in account is kept while it is in use and is
          erased with its link to your trips on request [period to be confirmed].
        </p>
      </LegalSection>

      <LegalSection id="rights" title="9. Your rights">
        <p>
          You can ask us to show you the data we hold about you, correct it, delete it, or stop contacting you, and you
          can withdraw your consent at any time. Write to <a href={`mailto:${LEGAL_CONTACT_EMAIL}`}>{LEGAL_CONTACT_EMAIL}</a>.
          The time within which we answer, and your right to complain to a data protection authority, are [to be
          confirmed].
        </p>
      </LegalSection>

      <LegalSection id="security" title="10. Security">
        <p>
          The site uses encrypted connections (HTTPS). Our database is not reachable from the internet, and access to it
          is limited to the systems that need it. No method is perfectly secure; if a breach affects your data we will
          inform you as the law requires.
        </p>
      </LegalSection>

      <LegalSection id="children" title="11. Children">
        <p>The Trip Planner is not meant for children. The minimum age is [to be confirmed].</p>
      </LegalSection>

      <LegalSection id="changes" title="12. Changes to this notice">
        <p>
          We will publish changes on this page with a new date. See also our{" "}
          <Link href={TERMS_PATH}>Terms of use</Link>.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
