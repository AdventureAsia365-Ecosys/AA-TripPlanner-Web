// AA-1041 (R7 item 6) — /plan/shared#v=1&s=<ids>: the read-only share / print view of a trip. The trip is in the
// fragment (component ids only, lib/shareTrip.ts), so this page is the same for everyone and holds nothing personal.
import type { Metadata } from "next";
import Header from "@/components/Header";
import SharedTripView from "@/components/SharedTripView";

export const metadata: Metadata = {
  title: "Shared trip",
  description: "A trip plan built from real Adventure Asia tours, shared read-only.",
  // A personal plan someone shared: keep it out of search results (as /plan).
  robots: { index: false, follow: false },
};

export default function SharedTripPage() {
  return (
    <div className="min-h-full bg-k-bg">
      <div className="tp-no-print">
        <Header />
      </div>
      <SharedTripView planHref="/plan" agencyName={null} />
    </div>
  );
}
