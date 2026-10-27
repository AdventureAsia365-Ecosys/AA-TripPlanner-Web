"use client";

import { setThemeChoice, useThemeChoice, type ThemeChoice } from "./theme";
import { Icon } from "./Icon";

const NEXT: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<ThemeChoice, string> = {
  system: "Theme: follows your device",
  light: "Theme: light",
  dark: "Theme: dark",
};

// AA-995: tone "band" = on the navy site header (light icon, white hairline, 44 px target).
const TONES = {
  default: "h-9 w-9 border-k-line text-k-muted hover:border-k-accent-border hover:text-k-accent-deep",
  band: "h-11 w-11 border-k-band-line text-k-on-band hover:border-k-band-accent hover:text-k-band-accent",
};

// One button cycling system → light → dark (like the CIS sidebar toggle, compact for a header).
export function ThemeToggle({ tone = "default" }: { tone?: keyof typeof TONES }) {
  const choice = useThemeChoice();
  return (
    <button
      type="button"
      onClick={() => setThemeChoice(NEXT[choice])}
      aria-label={`${LABEL[choice]}. Change theme`}
      title={LABEL[choice]}
      className={`inline-flex shrink-0 items-center justify-center rounded-full border transition ${TONES[tone]}`}
    >
      <Icon name={choice === "dark" ? "moon" : choice === "light" ? "sun" : "contrast"} className="h-4 w-4" />
    </button>
  );
}
