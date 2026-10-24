"use client";
// AA-908 — the browser's view of the traveller session: "signed in as <email>" or not, from GET /api/auth/status.
// The tokens themselves stay in HttpOnly cookies that only the BFF reads. One request per page load, shared by every
// component that asks (header link, send form).
import { useEffect, useState } from "react";
import { STORAGE_KEY } from "./guestIds";

export interface AccountStatus {
  loaded: boolean;
  signedIn: boolean;
  email: string | null;
  // false when sign-in is not configured on this deployment (a preview without TP_SESSION_SECRET)
  available: boolean;
}

const LOADING: AccountStatus = { loaded: false, signedIn: false, email: null, available: true };
let pending: Promise<AccountStatus> | null = null;

export function fetchAccountStatus(): Promise<AccountStatus> {
  if (!pending) {
    pending = fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => ({
        loaded: true,
        signedIn: b?.signed_in === true,
        email: typeof b?.email === "string" ? b.email : null,
        available: b?.signed_in === true || b?.available !== false,
      }))
      .catch(() => ({ ...LOADING, loaded: true }));
  }
  return pending;
}

export function useAccount(): AccountStatus {
  const [status, setStatus] = useState<AccountStatus>(LOADING);
  useEffect(() => {
    let live = true;
    fetchAccountStatus().then((s) => {
      if (live) setStatus(s);
    });
    return () => {
      live = false;
    };
  }, []);
  return status;
}

// Sign out: the BFF revokes the session and drops the guest cookie; this browser also forgets its trip ids, so the
// next visitor starts a new trip. Then a full page load.
export async function signOut(to: string = "/"): Promise<void> {
  try {
    await fetch("/api/auth/sign-out", { method: "POST" });
  } finally {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    pending = null;
    window.location.assign(to);
  }
}
