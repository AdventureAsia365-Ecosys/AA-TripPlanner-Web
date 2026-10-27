// AA-812 — tone badge (CIS kit Badge: pill, 11px uppercase, tone bg/fg pairs).
import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "success" | "info" | "danger";

const TONE: Record<Tone, string> = {
  neutral: "bg-k-neutral-bg text-k-neutral-fg",
  accent: "bg-k-accent-tint text-k-accent-deep",
  success: "bg-k-success-soft text-k-success",
  info: "bg-k-info-soft text-k-info",
  // AA-682 S5: Overdue payment chip (red on red-soft, checked by lib/contrast.test.ts).
  danger: "bg-k-danger-soft text-k-danger",
};

export function Badge({ children, tone = "neutral", className = "" }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.04em] ${TONE[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
