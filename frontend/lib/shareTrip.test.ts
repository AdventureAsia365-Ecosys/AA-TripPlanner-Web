// AA-1041 (R7 slice 1, item 6) — the share link and the calendar file (lib/shareTrip.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { decodeShare, encodeShare, parseStartDate, sharePath, shareUrl, tripIcs, MAX_SHARED_STOPS } from "./shareTrip.ts";

const A = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const B = "1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e";

test("encode / decode round trip keeps the order and the added days", () => {
  const frag = encodeShare([{ component_id: B }, { component_id: A.toUpperCase() }], [
    { kind: "extra_night", after_component_id: A },
    { kind: "free_day", after_component_id: null },
    { kind: "extend_end", after_component_id: null },
    { kind: "extra_night", after_component_id: "not-in-the-trip" },
  ]);
  assert.match(frag, /^v=1&s=/);
  assert.deepEqual(decodeShare(frag), {
    ids: [B, A],
    added: [{ kind: "extra_night", afterIndex: 1 }, { kind: "free_day", afterIndex: null },
      { kind: "extend_end", afterIndex: null }],
  });
  assert.deepEqual(decodeShare(`#${frag}`).ids, [B, A]);
});

test("a link carries ids only: text, bad ids, other versions and out-of-range nights are dropped", () => {
  assert.deepEqual(decodeShare("v=1&s=hello.<b>x</b>." + A + "&d=n5,zz,f"), {
    ids: [A], added: [{ kind: "free_day", afterIndex: null }],
  });
  assert.deepEqual(decodeShare("v=2&s=" + A), { ids: [], added: [] });
  assert.deepEqual(decodeShare(""), { ids: [], added: [] });
  assert.deepEqual(decodeShare(`v=1&s=${A}.${A}`).ids, [A]);
});

test("at most 50 stops", () => {
  const ids = Array.from({ length: 60 }, (_, i) => `${String(i).padStart(8, "0")}-0000-4000-8000-000000000000`);
  assert.equal(decodeShare(`v=1&s=${ids.join(".")}`).ids.length, MAX_SHARED_STOPS);
  assert.equal(decodeShare(encodeShare(ids.map((component_id) => ({ component_id })))).ids.length, MAX_SHARED_STOPS);
});

test("the share path follows the planner's tenant", () => {
  assert.equal(sharePath(null), "/plan/shared");
  assert.equal(sharePath("dravin-travel"), "/dravin-travel/plan/shared");
  assert.equal(shareUrl("https://tp.example/", "dravin-travel", "v=1&s="), "https://tp.example/dravin-travel/plan/shared#v=1&s=");
});

test("parseStartDate takes an <input type=date> value only", () => {
  assert.equal(parseStartDate("2027-03-14")?.toISOString(), "2027-03-14T00:00:00.000Z");
  assert.equal(parseStartDate("14/03/2027"), null);
  assert.equal(parseStartDate(""), null);
});

test("tripIcs: one all-day event per day, escaped, CRLF, folded", () => {
  const ics = tripIcs(
    [{ day: 1, title: "Dravin, market; walk", description: "Line one\nline two" },
      { day: 2, title: "A".repeat(100) }],
    parseStartDate("2027-12-31")!,
    "My trip",
    new Date("2027-01-02T03:04:05.678Z"),
  );
  const lines = ics.split("\r\n");
  assert.equal(lines[0], "BEGIN:VCALENDAR");
  assert.ok(ics.endsWith("END:VCALENDAR\r\n"));
  assert.ok(lines.includes("DTSTART;VALUE=DATE:20271231"));
  assert.ok(lines.includes("DTEND;VALUE=DATE:20280101"));
  assert.ok(lines.includes("DTSTART;VALUE=DATE:20280101"));       // day 2 crosses the year
  assert.ok(lines.includes("SUMMARY:Day 1: Dravin\\, market\\; walk"));
  assert.ok(lines.includes("DESCRIPTION:Line one\\nline two"));
  assert.ok(lines.includes("DTSTAMP:20270102T030405Z"));
  assert.ok(lines.every((l) => l.length <= 75), "folded at 75 octets");
  assert.equal(lines.filter((l) => l === "BEGIN:VEVENT").length, 2);
});
