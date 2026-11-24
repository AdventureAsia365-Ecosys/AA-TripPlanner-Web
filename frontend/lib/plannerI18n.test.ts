// AA-1160 (scope 2) — the planner's words follow the agency's locale (lib/planner.messages.ts,
// components/PlannerLang.tsx). Run: npm test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { missingTranslations, translator } from "../ui/core/i18n.ts";
import {
  FILTERS, LEAD, PLANNER, PLANNER_CATALOGS, ROUTES, TRIP, formatDaysIn, plannerLang,
} from "./planner.messages.ts";
import { BUDGET_BANDS, budgetLabel, leadResultMessage } from "./leadForm.ts";
import { brandCopy } from "./brand.ts";
import { legalPaths } from "./legal.ts";

const AGENCY = "Brackenford Trails";   // invented

test("every catalogue has a Vietnamese side for every English key", () => {
  for (const [name, m] of Object.entries(PLANNER_CATALOGS)) {
    assert.deepEqual(missingTranslations(m), [], name);
    assert.deepEqual(Object.keys(m.vi).sort(), Object.keys(m.en).sort(), name);
  }
});

test("the locale picks the language; anything but vi is English", () => {
  assert.equal(plannerLang("vi"), "vi");
  for (const other of ["en", "", null, undefined, "fr", "VI-x"]) assert.equal(plannerLang(other as string), "en");
  assert.equal(translator(TRIP, "vi")("emptyTitle"), "Bắt đầu xây dựng chuyến đi");
  assert.equal(translator(TRIP, "en")("emptyTitle"), "Start building your trip");
  assert.equal(translator(PLANNER, "vi")("tripPlanner"), "Lập kế hoạch chuyến đi");
  assert.equal(translator(ROUTES, "vi")("planFromRoute"), "Lên kế hoạch từ tuyến của tour");   // S290
  assert.equal(translator(ROUTES, "vi")("close"), "Đóng");
  assert.equal(formatDaysIn(1, "en"), "1 day");
  assert.equal(formatDaysIn(3, "en"), "3 days");
  assert.equal(formatDaysIn(3, "vi"), "3 ngày");
  assert.equal(translator(FILTERS, "vi")("value", "trekking"), "đi bộ đường dài");
  assert.equal(translator(FILTERS, "en")("value", "cultural_heritage"), "cultural heritage");
});

test("the lead form's answers and budget bands in Vietnamese, English unchanged by default", () => {
  assert.match(leadResultMessage(200, null, AGENCY, "vi"), new RegExp(`^Đã gửi cho ${AGENCY}\\.`));
  assert.match(leadResultMessage(200, null, AGENCY), new RegExp(`^Sent to ${AGENCY}\\.`));
  assert.match(leadResultMessage(409, { error: "lead_route_missing" }, AGENCY, "vi"), /liên hệ trực tiếp/);
  assert.equal(leadResultMessage(500, null, AGENCY, "vi"), translator(LEAD, "vi")("failed"));
  for (const b of BUDGET_BANDS) {
    assert.equal(budgetLabel(b.value), b.label);                     // the English labels stay BUDGET_BANDS'
    assert.ok(budgetLabel(b.value, "vi") && budgetLabel(b.value, "vi") !== b.value);
  }
});

test("the agency's brand words and legal links in Vietnamese name the agency only", () => {
  const vi = brandCopy(AGENCY, "vi");
  const en = brandCopy(AGENCY);
  assert.deepEqual(Object.keys(vi).sort(), Object.keys(en).sort());
  assert.deepEqual(Object.keys(vi.narration).sort(), Object.keys(en.narration).sort());
  assert.match(vi.consent, new RegExp(AGENCY));
  const text = JSON.stringify(vi) + vi.coverage.built(2) + vi.activities.onTours(2);
  assert.ok(!/Adventure Asia|adventure\.asia|\bAA\b/.test(text), text);
  assert.equal(brandCopy(null, "vi").agency, null);                  // Adventure Asia's own site stays English
  assert.deepEqual(legalPaths("brackenford", "vi").links.map((l) => l.label), ["Quyền riêng tư", "Điều khoản"]);
  assert.deepEqual(legalPaths("brackenford").links.map((l) => l.label), ["Privacy", "Terms"]);
});

test("the agency planner passes its locale to the planner, and the planner's chrome reads the catalogues", () => {
  const page = readFileSync(new URL("../app/[tenant]/plan/page.tsx", import.meta.url), "utf8");
  assert.match(page, /lang=\{plannerLang\(site\.locale\)\}/);
  const app = readFileSync(new URL("../app/plan/PlannerApp.tsx", import.meta.url), "utf8");
  assert.match(app, /<PlannerLangProvider lang=\{lang\}>/);
  // The S285 screenshots' English strings no longer sit in the components as literals.
  const sources: Record<string, string[]> = {
    "../components/TripPanel.tsx": ["Start building your trip", "Choose a country", "Day-by-day narration"],
    "../components/Header.tsx": [">\n          Trip Planner\n        </span>\n      </div>\n      <nav className=\"flex shrink-0"],
    "../components/LeadForm.tsx": ["Send my request", "Budget per person"],
    "../components/FilterChips.tsx": ["Search experiences — e.g.", "All countries"],
    "../components/RoutePlanner.tsx": ["Start from a tour route", "Find routes", "Plan from a tour route",
      "            Close\n"],   // S290: the open finder's heading and its close button too
    "../components/DayList.tsx": ["            Show on map", "\"Move to start\""],
  };
  for (const [file, literals] of Object.entries(sources)) {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    for (const lit of literals) assert.ok(!src.includes(lit), `${file} still has "${lit}"`);
  }
});
