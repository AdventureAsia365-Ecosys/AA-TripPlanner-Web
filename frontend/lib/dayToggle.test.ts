// AA-995 — "Expand all / Collapse all" on the tour timeline (lib/dayToggle.ts, components/site/DayToggleAll.tsx).
import { test } from "node:test";
import assert from "node:assert/strict";
import { allOpen, setAllOpen } from "./dayToggle.ts";

test("allOpen: every day open, and at least one day", () => {
  assert.equal(allOpen([]), false);
  assert.equal(allOpen([{ open: true }, { open: false }]), false);
  assert.equal(allOpen([{ open: true }, { open: true }]), true);
});

test("setAllOpen opens / closes every day and touches only the ones that change", () => {
  const writes: boolean[] = [];
  const day = (open: boolean) => {
    let v = open;
    return {
      get open() {
        return v;
      },
      set open(x: boolean) {
        writes.push(x);
        v = x;
      },
    };
  };
  const days = [day(true), day(false), day(false)];
  setAllOpen(days, true);
  assert.equal(allOpen(days), true);
  assert.deepEqual(writes, [true, true]); // day 1 was already open: no second toggle event
  setAllOpen(days, false);
  assert.equal(days.some((d) => d.open), false);
});
