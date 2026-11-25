"use client";
// AA-1038 R5 slice 2 — a /{slug} page whose tenant lookup could not answer (the browse Lambda failed or timed out,
// lib/tenantSite.ts TenantLookupUnavailable). Not the 404 page: the tenant may well exist, so the visitor gets a
// retryable error. Nothing is cached for a failed lookup, so "Try again" asks the browse Lambda again.
import { Button, ButtonLink, ErrorState } from "@/ui";
import { ErrorReference } from "@/ui/ErrorReference";

export default function TenantError({ error }: { error: Error & { digest?: string } }) {
  return (
    <main id="main" className="mx-auto max-w-md px-gutter py-section">
      <ErrorState title="We could not open this trip planner">
        Something went wrong on our side. Please try again in a moment.
        {/* AA-1111: the error's reference (its digest), never its message */}
        <ErrorReference digest={error.digest} />
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Button variant="primary" size="sm" onClick={() => window.location.reload()} data-testid="tenant-retry">
            Try again
          </Button>
          <ButtonLink href="/" variant="ghost" size="sm">Home</ButtonLink>
        </div>
      </ErrorState>
    </main>
  );
}
