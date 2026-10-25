"use client";
// AA-908 — "Sign in" / "My trips" in both headers. A client component: the kit pages are CDN-cached, so the
// signed-in state is fetched by the browser (/api/auth/status), never rendered into the cached HTML.
import Link from "next/link";
import { useAccount } from "@/lib/useAccount";

export default function AccountLink({ className = "" }: { className?: string }) {
  const account = useAccount();
  if (!account.loaded || !account.available) return null;
  const cls = `aa-focus px-2 text-[13px] font-medium text-k-body hover:text-k-accent-deep ${className}`;
  return account.signedIn ? (
    <Link href="/trips" className={cls} data-testid="account-link">
      My trips
    </Link>
  ) : (
    <Link href="/sign-in" className={cls} data-testid="account-link">
      Sign in
    </Link>
  );
}
