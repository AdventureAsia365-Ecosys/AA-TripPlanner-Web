// AA-908 — /trips: My Trips, the signed-in traveller's trips with their TripPlanner status (draft / sent to an
// advisor). Rendered in the browser (components/MyTrips.tsx): the page shell is the same for everyone and holds no
// personal data. Not indexed.
import type { Metadata } from "next";
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
      <main id="main" className="mx-auto max-w-3xl px-4 py-10">
        <MyTrips />
      </main>
      <SiteFooter />
    </div>
  );
}
