"use client";

import { useEffect, useState } from "react";
import { NOTICE_EVENT, takeResetNotice } from "@/lib/guestIds";

// AA-827: after a 401/403 the planner forgets its ids and reloads with a new trip
// (lib/guestIds.resetGuestIds); this says why, once, for a few seconds. It also shows notices raised
// while the page stays (guestIds.showGuestNotice, e.g. "cookies are needed"), 10 s each.
export default function GuestResetNotice() {
  const [notice, setNotice] = useState<{ text: string; ms: number; n: number } | null>(null);

  useEffect(() => {
    const t = takeResetNotice();
    if (t) setNotice({ text: t, ms: 6000, n: 0 });
    const onNotice = (e: Event) => {
      const text = (e as CustomEvent<string>).detail;
      if (typeof text === "string" && text) {
        setNotice((prev) => ({ text, ms: 10000, n: (prev?.n ?? 0) + 1 }));
      }
    };
    window.addEventListener(NOTICE_EVENT, onNotice);
    return () => window.removeEventListener(NOTICE_EVENT, onNotice);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), notice.ms);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (!notice) return null;
  return (
    <div className="pointer-events-none fixed inset-x-3 top-16 z-50" role="status">
      <div className="mx-auto max-w-md rounded-xl border border-aa-line bg-white px-3 py-2.5 text-xs text-aa-ink shadow-aa">
        {notice.text}
      </div>
    </div>
  );
}
