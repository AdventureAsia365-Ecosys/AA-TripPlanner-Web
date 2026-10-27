import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import { ButtonLink, EmptyState } from "@/ui";

export default function NotFound() {
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main" className="mx-auto max-w-xl px-4 py-16">
        <EmptyState title="We couldn't find that page">
          The tour or destination may have moved. Start from the home page or plan on the map.
        </EmptyState>
        <div className="mt-5 flex justify-center gap-2">
          <ButtonLink href="/" variant="secondary">Home</ButtonLink>
          <ButtonLink href="/plan" variant="primary">Plan a trip</ButtonLink>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
