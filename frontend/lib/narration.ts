// Day-by-day narration from POST /trip/{id}/narrate, split into per-day blocks for TripPanel.
//
// The text is "Day N: ..." blocks. A day taken from Adventure Asia's own itinerary is its title on
// the first line, then the prose ("Day 1: Arrival in Agra\nA 2-3 hour drive ..."); an AI-written
// day is one line of prose. AA-819 D3: the title is kept apart from the prose, so it no longer runs
// into it ("Arrival in Agra A 2-3 hour ...").

export interface NarrationBlock {
  day: number | null;
  title: string | null;
  text: string;
}

// A first line counts as a title when it is short and does not end like a sentence.
const TITLE_MAX = 90;

function splitTitle(body: string): { title: string | null; text: string } {
  const lines = body.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return { title: null, text: lines.join(" ") };
  const first = lines[0];
  if (first.length > TITLE_MAX || /[.!?…]$/.test(first)) return { title: null, text: lines.join("\n") };
  return { title: first, text: lines.slice(1).join("\n") };
}

// Defensively strips leftover markdown (**bold**, headings) from older/looser outputs.
export function parseNarration(raw: string): NarrationBlock[] {
  const clean = raw.replace(/\*\*/g, "").replace(/^#+\s*/gm, "").trim();
  if (!clean) return [];
  // Split before each "Day N" marker at the start of a line (keep the marker with its text).
  const parts = clean.split(/(?=^\s*Day\s+\d+\s*[:\-–])/im).map((p) => p.trim()).filter(Boolean);
  return parts.map((p) => {
    const m = p.match(/^Day\s+(\d+)\s*[:\-–]\s*([\s\S]*)$/i);
    if (!m) return { day: null, title: null, text: p };
    return { day: Number(m[1]), ...splitTitle(m[2]) };
  });
}
