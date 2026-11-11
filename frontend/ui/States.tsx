// AA-812 — empty and error states (one look for every kit page).
// AA-995 "Golden Hour": card radius, the icon in a soft round badge (gold for empty, red for error), body-size title and
// small muted text; an error card keeps role="alert" and gets the danger hairline. Same props as before, so every
// caller (home, destination, My Trips, My Payments, not-found) changes look without a code change.
import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function EmptyState({ title, children, icon = "compass" }: { title: string; children?: ReactNode; icon?: string }) {
  return (
    <div className="rounded-card border border-dashed border-k-accent-border bg-k-card px-5 py-10 text-center">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-k-accent-tint">
        <Icon name={icon} className="h-6 w-6 text-k-accent-deep" />
      </span>
      <p className="mt-4 text-body font-semibold text-k-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-small text-k-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", children }: { title?: string; children?: ReactNode }) {
  return (
    <div role="alert" className="rounded-card border border-k-danger-border bg-k-card px-5 py-10 text-center">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-k-danger-soft">
        <Icon name="info" className="h-6 w-6 text-k-danger" />
      </span>
      <p className="mt-4 text-body font-semibold text-k-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-small text-k-muted">{children}</div>}
    </div>
  );
}

// AA-1041 (R7 item 7): the loading state of the kit: grey bars in the card's shape, a status for screen readers, no
// spinner. Under reduced motion (html[data-motion="reduce"], app/globals.css) the shimmer stops.
export function LoadingState({ label = "Loading", rows = 3, compact = false }: { label?: string; rows?: number; compact?: boolean }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" data-testid="loading-state"
      className={`rounded-card border border-k-line bg-k-card ${compact ? "p-3" : "px-5 py-6"}`}>
      <span className="sr-only">{label}…</span>
      <div aria-hidden className="space-y-3">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-3">
            <span className="tp-skeleton h-10 w-10 shrink-0 rounded-tile" />
            <span className="flex-1 space-y-1.5">
              <span className="tp-skeleton block h-3 w-3/4 rounded-pill" />
              <span className="tp-skeleton block h-2.5 w-1/2 rounded-pill" />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
