// AA-812 — Adventure Asia's 404 page. AA-995: the Golden Hour title band (PageIntro) with the two ways back, then the
// ways in (WaysIn), so a lost visitor lands somewhere useful.
// AA-1115 follow-up: moved out of app/not-found.tsx. Next renders the root not-found into the RSC payload of EVERY page
// (the not-found boundary of the root layout), an agency's /{slug} pages included, so AA's bar, AI band and footer
// rode along inside an agency page's HTML. This body is now loaded only when the 404 is shown
// (components/site/AaNotFound.tsx, next/dynamic): the payload of any other page carries a reference to that client
// component and nothing of its text. Imported from a client module, so these components render as client components
// here; `preview` comes from the server (app/not-found.tsx), the browser has no SITE_INDEXING.
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import WaysIn from "@/components/site/WaysIn";
import { ButtonLink } from "@/ui";

export default function AaNotFoundPage({ preview }: { preview: boolean }) {
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
      <SiteFooter preview={preview} />
    </div>
  );
}
