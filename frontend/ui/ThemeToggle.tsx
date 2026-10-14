"use client";

import { setThemeChoice, useThemeChoice, type ThemeChoice } from "./theme";
import { Icon } from "./Icon";

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<ThemeChoice, string> = {
  system: "Theme: follows your device",
  light: "Theme: light",
  dark: "Theme: dark",
};

// One button cycling system → light → dark (like the CIS sidebar toggle, compact for a header).
export function ThemeToggle() {
  const choice = useThemeChoice();
  return (
    <button
      type="button"
      onClick={() => setThemeChoice(NEXT[choice])}
      aria-label={`${LABEL[choice]}. Change theme`}
      title={LABEL[choice]}
      className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-k-line text-k-muted transition hover:border-k-accent-border hover:text-k-accent-deep"
    >
      <Icon name={choice === "dark" ? "moon" : choice === "light" ? "sun" : "contrast"} className="h-4 w-4" />
    </button>
  );
}
