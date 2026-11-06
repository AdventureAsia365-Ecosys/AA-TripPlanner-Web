// AA-1038 R5 slice 2 — the /{slug}/plan planner fills the viewport like /plan (app/plan/layout.tsx).
export default function TenantPlanLayout({ children }: { children: React.ReactNode }) {
  return <div className="h-full">{children}</div>;
}
