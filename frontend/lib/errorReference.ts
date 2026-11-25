// AA-1111 (S294 ZAP triage) — what an HTML error page may show of an error: its digest (Next's id for a server error,
// logged with it), when it has the shape of one; never the message, the stack or anything else. Pure, `npm test`.
const DIGEST_RE = /^[A-Za-z0-9_-]{1,64}$/;

export function errorReference(digest: unknown): string | null {
  return typeof digest === "string" && DIGEST_RE.test(digest) ? digest : null;
}
