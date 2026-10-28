// AA-995 (spec §4.4, R10) — the map planner now uses the site face (Poppins, from the root layout), so no page loads
// Inter any more. The wrapper fills the viewport like <body> did, so the planner layout is unchanged.
export default function PlanLayout({ children }: { children: React.ReactNode }) {
  return <div className="h-full">{children}</div>;
}
