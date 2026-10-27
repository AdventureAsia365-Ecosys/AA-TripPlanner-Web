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
