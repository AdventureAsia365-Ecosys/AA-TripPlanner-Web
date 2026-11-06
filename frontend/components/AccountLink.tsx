"use client";
// AA-908 — "Sign in" / "My trips" in both headers. A client component: the kit pages are CDN-cached, so the
// signed-in state is fetched by the browser (/api/auth/status), never rendered into the cached HTML.
import Link from "next/link";
import { useAccount } from "@/lib/useAccount";
import { accountLinks } from "@/lib/plannerTenant";

// AA-995: tone "band" = light text on the navy site header.
export default function AccountLink({ className = "", tone = "default" }: { className?: string; tone?: "default" | "band" }) {
  const account = useAccount();
  if (!account.loaded || !account.available) return null;
  const color =
    tone === "band" ? "text-small text-k-on-band hover:text-k-band-accent" : "text-[13px] text-k-body hover:text-k-accent-deep";
  const cls = `aa-focus px-2 font-medium ${color} ${className}`;
  // AA-1038 slice 2: on a tenant's planner (/{slug}/plan) both links keep the tenant. Read only after the account
  // status loaded (client only; the server render above returned null), so it never differs between server and client.
  const links = accountLinks();
  return account.signedIn ? (
    <Link href={links.trips} className={cls} data-testid="account-link">
      My trips
    </Link>
  ) : (
    <Link href={links.signIn} className={cls} data-testid="account-link">
      Sign in
    </Link>
  );
}
