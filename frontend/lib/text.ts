// AA-812 — small text helpers for catalog content (server and client safe).

// "Private guide\n- Transfers; Breakfast" → ["Private guide", "Transfers", "Breakfast"]
export function splitLines(text: string | null | undefined): string[] {
  if (!text) return [];
  return text
    .split(/\r?\n|;|•/)
    .map((s) => s.replace(/^[\s\-*–—]+/, "").trim())
    .filter(Boolean);
}

// First ~max chars on a word boundary, for meta descriptions.
export function clip(text: string | null | undefined, max = 160): string {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 20))}…`;
}

export function daysText(n: number | null | undefined, raw?: string | null): string | null {
  if (n) return `${n} day${n > 1 ? "s" : ""}`;
  return raw || null;
}

export function titleCase(v: string): string {
  return v.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
