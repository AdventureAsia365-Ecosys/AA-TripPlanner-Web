"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { NOTICE_EVENT, takeResetNotice } from "@/lib/guestIds";

// AA-827: after a 401/403 the planner forgets its ids and reloads with a new trip
// (lib/guestIds.resetGuestIds); this says why, once, for a few seconds. It also shows notices raised
// while the page stays (guestIds.showGuestNotice, e.g. "cookies are needed"), 10 s each.
//
// AA-906 (React Compiler lint, no setState in an effect body): the reset notice left in
// sessionStorage is read through useSyncExternalStore — null on the server and during hydration,
// then the stored text — instead of being copied into state from a mount effect. It is taken from
// storage once per page load (pageNotice) and dropped when its timer ends or a live notice replaces it.
let pageNotice: string | null | undefined;
const pageNoticeListeners = new Set<() => void>();

function readPageNotice(): string | null {
  if (pageNotice === undefined) pageNotice = takeResetNotice();
  return pageNotice;
}

function dismissPageNotice(): void {
  if (!pageNotice) return;
  pageNotice = null;
  pageNoticeListeners.forEach((l) => l());
}

function subscribePageNotice(listener: () => void): () => void {
  pageNoticeListeners.add(listener);
  return () => pageNoticeListeners.delete(listener);
}

const noNoticeOnServer = () => null;

export default function GuestResetNotice() {
  const initial = useSyncExternalStore(subscribePageNotice, readPageNotice, noNoticeOnServer);
  const [live, setLive] = useState<{ text: string; n: number } | null>(null);

  useEffect(() => {
    const onNotice = (e: Event) => {
      const text = (e as CustomEvent<string>).detail;
      if (typeof text === "string" && text) {
        dismissPageNotice();
        setLive((prev) => ({ text, n: (prev?.n ?? 0) + 1 }));
      }
    };
    window.addEventListener(NOTICE_EVENT, onNotice);
    return () => window.removeEventListener(NOTICE_EVENT, onNotice);
  }, []);

  // A live notice wins over the page-load one; `n` restarts the timer when the same text comes again.
  const notice = live
    ? { text: live.text, ms: 10000, id: `live-${live.n}` }
    : initial
      ? { text: initial, ms: 6000, id: "page" }
      : null;
  const noticeId = notice?.id ?? null;
  const noticeMs = notice?.ms ?? 0;

  useEffect(() => {
    if (!noticeId) return;
    const timer = window.setTimeout(() => {
      dismissPageNotice();
      setLive(null);
    }, noticeMs);
    return () => window.clearTimeout(timer);
  }, [noticeId, noticeMs]);

  if (!notice) return null;
  return (
    <div className="pointer-events-none fixed inset-x-3 top-16 z-50" role="status">
      <div className="mx-auto max-w-md rounded-xl border border-aa-line bg-white px-3 py-2.5 text-xs text-aa-ink shadow-aa">
        {notice.text}
      </div>
    </div>
  );
}
