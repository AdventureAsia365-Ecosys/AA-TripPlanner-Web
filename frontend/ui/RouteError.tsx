"use client";

// AA-812 — error boundary body for the catalog pages (a failed browse read, not a missing page).
// AA-995: the Golden Hour title band (PageIntro); the message keeps role="alert" (as ErrorState gave it).
import { useEffect } from "react";
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import WaysIn from "@/components/site/WaysIn";
import { ButtonLink, buttonClass } from "@/ui";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro
          narrow
          kicker="A short detour"
          title="This page couldn't load right now"
          lead="Please try again in a moment."
          leadRole="alert"
        >
          <div className="flex flex-col justify-center gap-3 sm:flex-row">
            <button type="button" onClick={reset} className={buttonClass("primary", "lg", "min-h-12")}>Try again</button>
            <ButtonLink href="/" variant="on-dark" size="lg" className="min-h-12">Home</ButtonLink>
          </div>
        </PageIntro>
        <WaysIn />
      </main>
      <SiteFooter />
    </div>
  );
}
