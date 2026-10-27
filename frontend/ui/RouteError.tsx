"use client";

// AA-812 — error boundary body for the catalog pages (a failed browse read, not a missing page).
import { useEffect } from "react";
import Link from "next/link";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import { ErrorState, buttonClass } from "@/ui";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main" className="mx-auto max-w-xl px-4 py-16">
        <ErrorState title="This page couldn't load right now">Please try again in a moment.</ErrorState>
        <div className="mt-5 flex justify-center gap-2">
          <button type="button" onClick={reset} className={buttonClass("primary")}>Try again</button>
          <Link href="/" className={buttonClass("secondary")}>Home</Link>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
