// AA-995 — the map planner keeps Inter for now (judges' graft on spec R10): moving it to Poppins is its own
// slice with 390 px screenshots of TripPanel / FilterChips. Loading Inter here means the customer pages
// no longer download it. The wrapper fills the viewport like <body> did, so the planner layout is unchanged.
import { Inter } from "next/font/google";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });

export default function PlanLayout({ children }: { children: React.ReactNode }) {
  return <div className={`${inter.variable} tp-planner-font h-full`}>{children}</div>;
}
