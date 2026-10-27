// AA-812 — the 404 page. AA-995: the Golden Hour title band (PageIntro) with the two ways back, then the ways in
// (WaysIn), so a lost visitor lands somewhere useful.
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import WaysIn from "@/components/site/WaysIn";
import { ButtonLink } from "@/ui";

export default function NotFound() {
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro
          narrow
          kicker="Off the trail"
          title="We couldn't find that page"
          lead="The tour or destination may have moved. Start from the home page or plan on the map."
        >
          <div className="flex flex-col justify-center gap-3 sm:flex-row">
            <ButtonLink href="/plan" variant="primary" size="lg" className="min-h-12">Plan a trip</ButtonLink>
            <ButtonLink href="/" variant="on-dark" size="lg" className="min-h-12">Home</ButtonLink>
          </div>
        </PageIntro>
        <WaysIn />
      </main>
      <SiteFooter />
    </div>
  );
}
