// AA-908 — /sign-in: traveller sign-in with an email code only (no password; root ADR 0006 decisions 1 and 4). The
// same form signs a new traveller up: the code proves the address either way. Not indexed.
// AA-995: the Golden Hour title band (PageIntro, the page's h1), the form card on the sand surface, and three lines on
// what an account does today (only what /trips and the email-code flow really do).
import type { Metadata } from "next";
import PageIntro from "@/components/site/PageIntro";
import SiteFooter from "@/components/site/SiteFooter";
import SiteHeader from "@/components/site/SiteHeader";
import SignInForm from "@/components/SignInForm";
import { safeNext } from "@/lib/authRequest";
import { Icon } from "@/ui";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to the Adventure Asia Trip Planner with a code sent to your email.",
  robots: { index: false, follow: false },
};

const WHY = [
  "Your trips are kept with your account",
  "Open them on any device",
  "No password: we email you a code",
];

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return (
    <div className="aa-site">
      <SiteHeader />
      <main id="main">
        <PageIntro narrow kicker="Welcome" title="Sign in" lead="Keep your Adventure Asia trips in one place." />
        <div className="bg-k-sand py-section">
          <div className="mx-auto max-w-md px-gutter">
            <SignInForm next={safeNext(next)} />
            <ul className="mt-6 space-y-2 text-small text-k-body">
              {WHY.map((w) => (
                <li key={w} className="flex items-start gap-2">
                  <Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-k-accent-deep" /> {w}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
