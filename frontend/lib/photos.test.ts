// AA-995 — photo rules of the customer pages: duplicate covers, grid order, ribbon content and fill.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  firstUseOfCover,
  photoKey,
  ribbonMode,
  ribbonRepeats,
  RIBBON_FILL_PX,
  RIBBON_STRIDE_PX,
  sortForGrid,
  uniqueRibbonItems,
} from "./photos.ts";

const P = "https://api.example/content/photos";

test("photoKey ignores the size query, so small and large of one photo are the same cover", () => {
  assert.equal(photoKey(`${P}/a?size=small`), photoKey(`${P}/a?size=large`));
  assert.equal(photoKey(null), null);
  assert.equal(photoKey(""), null);
});

test("a repeated cover is shown only the first time; missing covers are never shown", () => {
  const tours = [`${P}/bhutan?size=small`, `${P}/lanka?size=small`, `${P}/bhutan?size=small`, null, `${P}/lanka?size=large`];
  assert.deepEqual(firstUseOfCover(tours, (t) => t), [true, true, false, false, false]);
});

test("destinations grid: photos first, then most tours, then name", () => {
  const rows = [
    { country: "Laos", tour_count: 9, cover: null },
    { country: "Bhutan", tour_count: 5, cover: "b" },
    { country: "China", tour_count: 7, cover: "c" },
    { country: "Cambodia", tour_count: 9, cover: null },
  ];
  assert.deepEqual(sortForGrid(rows, (r) => !!r.cover).map((r) => r.country), ["China", "Bhutan", "Cambodia", "Laos"]);
});

test("ribbon items: no null photo, no duplicate photo, order kept", () => {
  const items = [
    { href: "/a", src: `${P}/1?size=small`, alt: "a", caption: "a" },
    { href: "/b", src: null, alt: "b", caption: "b" },
    { href: "/c", src: `${P}/1?size=large`, alt: "c", caption: "c" },
    { href: "/d", src: `${P}/2?size=small`, alt: "d", caption: "d" },
  ];
  assert.deepEqual(uniqueRibbonItems(items).map((i) => i.href), ["/a", "/d"]);
});

test("ribbon mode: >= 8 moves, 4-7 stays still, < 4 is left out", () => {
  assert.equal(ribbonMode(3), "none");
  assert.equal(ribbonMode(4), "static");
  assert.equal(ribbonMode(7), "static");
  assert.equal(ribbonMode(8), "animated");
  assert.equal(ribbonMode(30), "animated");
});

test("fill rule: one copy of the moving track is always >= 2400 px wide", () => {
  for (const n of [8, 9, 10, 12, 20]) {
    const reps = ribbonRepeats(n);
    assert.ok(n * reps * RIBBON_STRIDE_PX >= RIBBON_FILL_PX, `n=${n} reps=${reps}`);
    assert.ok(n * (reps - 1) * RIBBON_STRIDE_PX < RIBBON_FILL_PX, `n=${n} repeats more than needed`);
  }
  assert.equal(ribbonRepeats(0), 0);
});
