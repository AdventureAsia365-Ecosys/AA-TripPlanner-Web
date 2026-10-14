// AA-812 — empty and error states (one look for every kit page).
import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function EmptyState({ title, children, icon = "compass" }: { title: string; children?: ReactNode; icon?: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-k-line bg-k-card px-5 py-8 text-center">
      <Icon name={icon} className="mx-auto h-7 w-7 text-k-accent" />
      <p className="mt-3 text-sm font-semibold text-k-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-k-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", children }: { title?: string; children?: ReactNode }) {
  return (
    <div role="alert" className="rounded-2xl border border-k-line bg-k-card px-5 py-8 text-center">
      <Icon name="info" className="mx-auto h-7 w-7 text-k-danger" />
      <p className="mt-3 text-sm font-semibold text-k-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-[13px] leading-relaxed text-k-muted">{children}</div>}
    </div>
  );
}
