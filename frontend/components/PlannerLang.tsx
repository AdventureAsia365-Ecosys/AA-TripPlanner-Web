"use client";
// AA-1160 (scope 2) — the planner's language: the agency's locale on /{slug}/plan (app/[tenant]/plan/page.tsx passes
// it), English everywhere else (no provider). A context, not <html lang> read at render: the server render already
// carries the agency's words, so the first client render matches it (no hydration mismatch).
import { createContext, useContext, type ReactNode } from "react";
import { translator, type Lang, type Translator } from "@/lib/planner.messages";
import type { Catalog, Messages } from "@/ui/core/i18n";

const PlannerLangContext = createContext<Lang>("en");

export function PlannerLangProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  return <PlannerLangContext.Provider value={lang}>{children}</PlannerLangContext.Provider>;
}

export function usePlannerLang(): Lang {
  return useContext(PlannerLangContext);
}

/** The `t` of one planner catalogue (lib/planner.messages.ts) in the planner's language. */
export function usePlannerT<E extends Catalog>(messages: Messages<E>): Translator<E> {
  return translator(messages, useContext(PlannerLangContext));
}
