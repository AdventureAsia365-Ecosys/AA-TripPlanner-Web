// AA-908 — /sign-in: traveller sign-in with an email code only (no password; root ADR 0006 decisions 1 and 4). The
// same form signs a new traveller up: the code proves the address either way. Not indexed.
import type { Metadata } from "next";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import SignInForm from "@/components/SignInForm";
import { safeNext } from "@/lib/authRequest";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to the Adventure Asia Trip Planner with a code sent to your email.",
  robots: { index: false, follow: false },
};

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main" className="mx-auto max-w-md px-4 py-12">
        <SignInForm next={safeNext(next)} />
      </main>
      <SiteFooter />
    </div>
  );
}
