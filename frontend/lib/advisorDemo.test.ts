// AA-1034 R0 (root ADR 0019, B2B-only model): "Send to an advisor" is a demo by default. The button shows the demo
// notice and never calls the hand-off; a 410 b2b_only from the Lambda (old bundle) shows the same notice.
// Run: npm test (node --test with type stripping; no extra dependencies).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DEMO_NOTICE, isAdvisorDemo, isB2bOnly } from "./advisorDemo.ts";

const src = (rel: string) => readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");

test("the demo is on unless NEXT_PUBLIC_ADVISOR_HANDOFF is exactly live", () => {
  for (const raw of [undefined, "", "demo", "on", "true", "lvie"]) assert.equal(isAdvisorDemo(raw), true, String(raw));
  for (const raw of ["live", " LIVE "]) assert.equal(isAdvisorDemo(raw), false, raw);
  assert.match(src("lib/advisorDemo.ts"), /isAdvisorDemo\(process\.env\.NEXT_PUBLIC_ADVISOR_HANDOFF\)/);
});

test("only a 410 with error b2b_only is the demo answer", () => {
  assert.equal(isB2bOnly(410, { error: "b2b_only" }), true);
  assert.equal(isB2bOnly(410, { error: "gone" }), false);
  assert.equal(isB2bOnly(409, { error: "b2b_only" }), false);
  assert.equal(isB2bOnly(410, null), false);
});

test("the notice is plain English and names no internal id", () => {
  assert.match(DEMO_NOTICE.title, /demo/i);
  assert.match(DEMO_NOTICE.body, /travel agenc/);
  assert.doesNotMatch(DEMO_NOTICE.title + DEMO_NOTICE.body, /AA-\d+|b2b_only/);
});

test("TripPanel answers the demo before calling send and renders the notice", () => {
  const panel = src("components/TripPanel.tsx");
  const handler = panel.slice(panel.indexOf("const handleSend"), panel.indexOf("const tooLong"));
  assert.ok(
    handler.indexOf("if (ADVISOR_HANDOFF_DEMO)") >= 0 &&
      handler.indexOf("if (ADVISOR_HANDOFF_DEMO)") < handler.indexOf("await send("),
    "the demo check must come before the hand-off call",
  );
  assert.match(handler, /if \(res\.demo\)/);
  assert.match(panel, /data-testid="advisor-demo-notice"/);
  assert.match(src("lib/useTrip.tsx"), /if \(isB2bOnly\(code, body\)\) return \{ ok: false, needsRegistration: false, demo: true \};/);
});
