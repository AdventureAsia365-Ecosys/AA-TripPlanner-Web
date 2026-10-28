"use client";
// AA-995 (spec §4.3 row 7, optional) — "Expand all / Collapse all" for the tour's day-by-day timeline. The days stay
// native <details> (the server page works without JS); this button only opens or closes every one of them in the list
// it controls. Its label follows the list: "Collapse all" once every day is open (by this button or one by one), else
// "Expand all". Before hydration it is invisible but keeps its space, so no layout shift and no dead button without JS.
import { useCallback, useSyncExternalStore } from "react";
import { Icon } from "@/ui";
import { allOpen, setAllOpen } from "@/lib/dayToggle";

type State = "open" | "closed" | "server";

function days(listId: string): HTMLDetailsElement[] {
  const list = document.getElementById(listId);
  return list ? Array.from(list.querySelectorAll<HTMLDetailsElement>("details")) : [];
}

export default function DayToggleAll({ listId }: { listId: string }) {
  const subscribe = useCallback(
    (cb: () => void) => {
      const list = document.getElementById(listId);
      // "toggle" does not bubble; a capture listener on the list still sees every day's toggle.
      list?.addEventListener("toggle", cb, true);
      return () => list?.removeEventListener("toggle", cb, true);
    },
    [listId],
  );
  const state = useSyncExternalStore<State>(
    subscribe,
    () => (allOpen(days(listId)) ? "open" : "closed"),
    () => "server",
  );
  const open = state === "open";

  return (
    <button
      type="button"
      aria-controls={listId}
      onClick={() => setAllOpen(days(listId), !open)}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-pill px-2 text-small font-semibold text-k-accent-deep hover:underline ${
        state === "server" ? "invisible" : ""
      }`}
    >
      {open ? "Collapse all" : "Expand all"}
      <Icon name="chevron" className={`h-4 w-4 transition-transform duration-base ease-std ${open ? "rotate-180" : ""}`} />
    </button>
  );
}
