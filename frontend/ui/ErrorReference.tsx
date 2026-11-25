// AA-1111 (S294 ZAP triage) — the reference an error page shows: the digest Next logs with a server error (the request
// id of an HTML page), never the error's message or stack. A client-side error has no digest: nothing is shown.
import { errorReference } from "@/lib/errorReference";

// `tone`: "band" on the dark title band of the catalog error page (PageIntro), else the card's muted text.
export function ErrorReference({ digest, tone = "card" }: { digest?: string; tone?: "card" | "band" }) {
  const ref = errorReference(digest);
  if (!ref) return null;
  return (
    <p className={`mb-3 mt-3 text-small ${tone === "band" ? "text-k-on-band-muted" : "text-k-muted"}`} data-testid="error-reference">
      Reference: <span className="font-mono">{ref}</span>
    </p>
  );
}
