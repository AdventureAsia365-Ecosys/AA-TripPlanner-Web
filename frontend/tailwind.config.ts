import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./ui/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // AA-812: kit tokens (ui/tokens.css, same values as the CIS kit), theme-aware.
        // New pages use `k-*`; the map planner keeps the light-only `aa-*` hex palette.
        k: {
          accent: "var(--aa-accent)",
          "accent-deep": "var(--aa-accent-deep)",
          "accent-soft": "var(--aa-accent-soft)",
          "accent-tint": "var(--aa-accent-tint)",
          "accent-border": "var(--aa-accent-border)",
          slate: "var(--aa-slate)",
          danger: "var(--aa-red)",
          "danger-soft": "var(--aa-red-soft)",
          success: "var(--aa-green)",
          "success-soft": "var(--aa-green-soft)",
          info: "var(--aa-info)",
          "info-soft": "var(--aa-info-soft)",
          "neutral-bg": "var(--aa-neutral-bg)",
          "neutral-fg": "var(--aa-neutral-fg)",
          ink: "var(--aa-ink)",
          ink2: "var(--aa-ink2)",
          body: "var(--aa-body)",
          muted: "var(--aa-muted)",
          muted2: "var(--aa-muted2)",
          bg: "var(--aa-bg)",
          card: "var(--aa-card)",
          line: "var(--aa-line)",
          line2: "var(--aa-line2)",
          "on-accent": "var(--aa-on-accent)",
          scrim: "var(--aa-scrim)",
        },
        aa: {
          gold: "#DB9628",
          "gold-dark": "#B87A1A",
          "gold-soft": "#FBF1DE",
          ink: "#1F2933",
          "ink-soft": "#33363D",
          offwhite: "#F8F6F2",
          sand: "#F4F1EC",
          line: "#E6E0D8",
          muted: "#6B7280",
        },
      },
      fontFamily: {
        display: ["var(--aa-font-display)"],
        ui: ["var(--aa-font-sans)"],
        sans: [
          "var(--font-inter)",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
      },
      boxShadow: {
        aa: "0 1px 2px rgba(31,41,51,0.06), 0 6px 20px rgba(31,41,51,0.10)",
        "aa-sm": "0 1px 2px rgba(31,41,51,0.06), 0 2px 8px rgba(31,41,51,0.07)",
      },
    },
  },
  plugins: [],
};

export default config;
