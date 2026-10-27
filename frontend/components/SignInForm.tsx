"use client";
// AA-908 — the two-step email-code form of /sign-in: email -> POST /api/auth/start, code -> POST /api/auth/verify.
// The BFF holds every token; this form only sees "code sent", "signed in" or an error code.
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { Button } from "@/ui";
import { LEGAL_LINKS } from "@/lib/legal";

const MESSAGES: Record<string, string> = {
  invalid_email: "Please enter a valid email address.",
  code_invalid: "That code is not right. Check the latest email and try again.",
  code_expired: "That code has expired. Send a new one.",
  try_later: "Too many attempts. Please wait a few minutes and try again.",
  sign_up_unavailable: "Sign-up is not open right now. You can still plan and send your trip as a guest.",
  sign_in_unavailable: "Sign-in is not available on this site yet. You can still plan and send your trip as a guest.",
  delivery_failed: "We could not send the email. Check the address and try again.",
};
const GENERIC = "Something went wrong. Please try again.";

async function post(path: string, body: unknown): Promise<{ status: number; data: Record<string, unknown> }> {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: res.status, data: await res.json().catch(() => ({})) };
  } catch {
    return { status: 0, data: {} };
  }
}

export default function SignInForm({ next }: { next: string }) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const sendCode = async (e?: FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    const { status, data } = await post("/api/auth/start", { email });
    setBusy(false);
    if (status === 200) {
      setStep("code");
      setCode("");
      setNotice(`We sent a code to ${email.trim()}. It can take a minute to arrive.`);
    } else {
      setError(MESSAGES[String(data.error)] ?? GENERIC);
    }
  };

  const checkCode = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { status, data } = await post("/api/auth/verify", { code });
    if (status === 200 && data.signed_in === true) {
      window.location.assign(next);
      return;
    }
    setBusy(false);
    if (status === 200 && data.resent === true) {
      setCode("");
      setNotice(data.mode === "signup"
        ? "We sent you a new code. Enter it to finish creating your account."
        : "Your email is confirmed. We sent one more code to sign you in.");
      return;
    }
    if (data.error === "code_expired") setStep("email");
    setError(MESSAGES[String(data.error)] ?? GENERIC);
  };

  return (
    // AA-995: the page's h1 is the title band (app/sign-in/page.tsx); the card carries the form.
    <div className="rounded-panel border border-k-line bg-k-card p-6 shadow-e3 md:p-8">
      <h2 className="text-h3 text-k-ink">Sign in or create an account</h2>
      <p className="mt-2 text-small text-k-muted">
        Keep your trips and open them on any device. We email you a code, so there is no password to remember. New
        here? The same code creates your account.
      </p>

      {step === "email" ? (
        <form onSubmit={sendCode} className="mt-6 space-y-3">
          <label className="block text-small font-semibold text-k-ink" htmlFor="signin-email">
            Email
          </label>
          <input
            id="signin-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="aa-focus min-h-12 w-full rounded-tile border border-k-line bg-k-card px-4 text-body text-k-ink placeholder:text-k-muted"
            placeholder="you@example.com"
          />
          <Button type="submit" size="lg" disabled={busy || !email.trim()} className="min-h-12 w-full">
            {busy ? "Sending…" : "Email me a code"}
          </Button>
        </form>
      ) : (
        <form onSubmit={checkCode} className="mt-6 space-y-3">
          {notice && (
            <p role="status" className="rounded-tile bg-k-accent-tint px-4 py-3 text-small font-medium text-k-accent-deep">
              {notice}
            </p>
          )}
          <label className="block text-small font-semibold text-k-ink" htmlFor="signin-code">
            Code
          </label>
          <input
            id="signin-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6,8}"
            required
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
            className="aa-focus min-h-12 w-full rounded-tile border border-k-line bg-k-card px-4 text-center text-h3 tracking-[0.3em] text-k-ink"
          />
          <Button type="submit" size="lg" disabled={busy || code.length < 6} className="min-h-12 w-full">
            {busy ? "Checking…" : "Sign in"}
          </Button>
          <div className="flex justify-between text-small">
            <button type="button" className="aa-focus min-h-11 text-k-muted hover:text-k-ink" onClick={() => setStep("email")}>
              Use another email
            </button>
            <button type="button" className="aa-focus min-h-11 font-semibold text-k-accent-deep hover:underline"
              disabled={busy} onClick={() => sendCode()}>
              Send a new code
            </button>
          </div>
        </form>
      )}

      {error && (
        <p role="alert" className="mt-3 rounded-tile bg-k-danger-soft px-4 py-3 text-small font-medium text-k-danger">
          {error}
        </p>
      )}

      <p className="mt-6 border-t border-k-line pt-4 text-caption font-normal text-k-muted">
        How we use your email:{" "}
        {LEGAL_LINKS.map((l, i) => (
          <span key={l.href}>
            {i > 0 && " · "}
            <Link href={l.href} className="aa-focus font-semibold text-k-ink underline underline-offset-2">
              {l.label}
            </Link>
          </span>
        ))}{" "}
        (draft)
      </p>
    </div>
  );
}
