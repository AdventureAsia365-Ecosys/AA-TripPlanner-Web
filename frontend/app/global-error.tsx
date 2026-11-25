"use client";
// AA-1111 (S294 ZAP triage) — the last error page: an error the root layout itself cannot render (every page without
// an error.tsx of its own, the planner included). It replaces Next's default page with the kit's ErrorState: a
// generic message and the error's reference (ui/ErrorReference.tsx), never its message or stack. It can wrap an
// agency's page (/{slug}/... or its own domain), so it names no brand (lib/agencyBrand.test.ts walks it).
import "../ui/core/tokens.css";
import "../ui/tokens.css";
import "./globals.css";
import { useEffect } from "react";
import { Button, ErrorState } from "@/ui";
import { ErrorReference } from "@/ui/ErrorReference";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <html lang="en" data-theme="light">
      <body>
        <main id="main" className="mx-auto max-w-md px-gutter py-section">
          <ErrorState title="This page couldn't load right now">
            Something went wrong on our side. Please try again in a moment.
            <ErrorReference digest={error.digest} />
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              <Button variant="primary" size="sm" onClick={() => window.location.reload()} data-testid="global-retry">
                Try again
              </Button>
            </div>
          </ErrorState>
        </main>
      </body>
    </html>
  );
}
