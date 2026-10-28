// AA-908 — /trips: My Trips, the signed-in traveller's trips with their TripPlanner status (draft / sent to an
// advisor). Rendered in the browser (components/MyTrips.tsx): the page shell is the same for everyone and holds no
// personal data. Not indexed.
// AA-995: the Golden Hour title band (PageIntro, the page's h1 in every state, signed in or not), then the account
// content on the sand surface in a readable column.
import type { Metadata } from "next";
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import MyTrips from "@/components/MyTrips";

export const metadata: Metadata = {
  title: "My trips",
  description: "Your saved Adventure Asia trips.",
  robots: { index: false, follow: false },
};

export default function TripsPage() {
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro
          title="My trips"
          kicker="Your journeys"
          crumbs={[{ href: "/", label: "Home" }]}
          lead="Trips you planned while signed in. Open one to keep shaping it on the map."
        />
        <div className="bg-k-sand py-section">
          {/* min-h-72 (288 px) holds the slot of the sign-in prompt (274 px at 390 and 1440) while the account check
              runs, so the footer does not jump when it lands (S255: CLS 0.15 at 390 without it). */}
          <div className="mx-auto min-h-72 max-w-3xl px-gutter">
            <MyTrips />
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
