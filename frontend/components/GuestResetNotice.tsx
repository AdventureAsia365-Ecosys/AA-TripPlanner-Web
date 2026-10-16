"use client";

import { useEffect, useState } from "react";
import { takeResetNotice } from "@/lib/guestIds";

// AA-827: after a 401/403 the planner forgets its ids and reloads with a new trip
// (lib/guestIds.resetGuestIds); this says why, once, for a few seconds.
export default function GuestResetNotice() {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    const t = takeResetNotice();
    if (t) setText(t);
  }, []);

  useEffect(() => {
    if (!text) return;
    const timer = window.setTimeout(() => setText(null), 6000);
    return () => window.clearTimeout(timer);
  }, [text]);

  if (!text) return null;
  return (
    <div className="pointer-events-none fixed inset-x-3 top-16 z-50" role="status">
      <div className="mx-auto max-w-md rounded-xl border border-aa-line bg-white px-3 py-2.5 text-xs text-aa-ink shadow-aa">
        {text}
      </div>
    </div>
  );
}
