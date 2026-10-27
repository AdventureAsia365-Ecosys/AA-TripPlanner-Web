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
        // Every page uses `k-*`, the map planner included (AA-837 retired the light-only `aa-*` hex
        // palette). Colours are CSS variables, so Tailwind opacity modifiers (`/40`) do not apply:
        // pick a token instead (e.g. `k-accent-border` for a soft gold border).
        k: {
          accent: "var(--aa-accent)",
          "accent-deep": "var(--aa-accent-deep)",
          "accent-soft": "var(--aa-accent-soft)",
          "accent-tint": "var(--aa-accent-tint)",
          "accent-border": "var(--aa-accent-border)",
          slate: "var(--aa-slate)",
          danger: "var(--aa-red)",
          "danger-soft": "var(--aa-red-soft)",
          "danger-border": "var(--aa-red-border)",
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
          "hero-bg": "var(--aa-hero-bg)",
          // AA-995 B2C surfaces (--tp-*): sand band, navy band and the text that sits on navy.
          sand: "var(--tp-surface-sand)",
          band: "var(--tp-band)",
          "on-band": "var(--tp-on-band)",
          "on-band-muted": "var(--tp-on-band-muted)",
          "band-accent": "var(--tp-band-accent)",
          "band-line": "var(--tp-band-line)",
          "band-edge": "var(--tp-band-edge)",
        },
      },
      fontFamily: {
        display: ["var(--aa-font-display)"],
        ui: ["var(--aa-font-sans)"],
        // AA-995: Satisfy, section titles only (>= 28 px, <= 6 words).
        script: ["var(--tp-font-script)"],
        // Site pages: Poppins. The planner sets Inter on its own wrapper (app/plan/layout.tsx).
        sans: ["var(--aa-font-sans)"],
      },
      // AA-995 type scale (ui/tokens.css). New names only: Tailwind's own text-sm/lg… stay as they are.
      fontSize: {
        display: ["var(--aa-text-display)", { lineHeight: "1.1", letterSpacing: "-0.015em", fontWeight: "600" }],
        h1: ["var(--aa-text-h1)", { lineHeight: "1.15", letterSpacing: "-0.01em", fontWeight: "600" }],
        h3: ["var(--aa-text-h3)", { lineHeight: "1.3", fontWeight: "600" }],
        lead: ["var(--aa-text-lead)", { lineHeight: "1.6" }],
        body: ["var(--aa-text-body)", { lineHeight: "1.65" }],
        small: ["var(--aa-text-small)", { lineHeight: "1.55" }],
        caption: ["var(--aa-text-caption)", { lineHeight: "1.4", fontWeight: "500" }],
        kicker: ["var(--aa-text-kicker)", { lineHeight: "1.2", letterSpacing: "0.16em", fontWeight: "600" }],
        script: ["var(--tp-text-script)", { lineHeight: "1.05", fontWeight: "400" }],
        "script-sm": ["var(--tp-text-script-sm)", { lineHeight: "1.1", fontWeight: "400" }],
      },
      // New names, so the planner's rounded-lg / rounded-xl keep their Tailwind values.
      borderRadius: {
        chip: "var(--aa-radius-sm)",
        tile: "var(--aa-radius-md)",
        card: "var(--aa-radius-lg)",
        panel: "var(--aa-radius-xl)",
        pill: "var(--aa-radius-pill)",
      },
      spacing: {
        gutter: "var(--tp-gutter)",
        section: "var(--tp-section-y)",
        header: "var(--tp-header-h)",
      },
      maxWidth: {
        page: "var(--tp-container)",
      },
      zIndex: {
        header: "var(--aa-z-header)",
        "sticky-cta": "var(--aa-z-sticky-cta)",
        popover: "var(--aa-z-popover)",
      },
      transitionDuration: {
        instant: "var(--aa-dur-instant)",
        fast: "var(--aa-dur-fast)",
        base: "var(--aa-dur-base)",
        slow: "var(--aa-dur-slow)",
        reveal: "var(--aa-dur-reveal)",
      },
      transitionTimingFunction: {
        std: "var(--aa-ease-std)",
        out: "var(--aa-ease-out)",
        in: "var(--aa-ease-in)",
      },
      boxShadow: {
        // AA-995 elevation scale (theme-aware: darker geometry in dark mode).
        e1: "var(--aa-shadow-sm)",
        e2: "var(--aa-shadow-md)",
        e3: "var(--aa-shadow-lg)",
        aa: "0 1px 2px rgba(31,41,51,0.06), 0 6px 20px rgba(31,41,51,0.10)",
        "aa-sm": "0 1px 2px rgba(31,41,51,0.06), 0 2px 8px rgba(31,41,51,0.07)",
      },
    },
  },
  plugins: [],
};

export default config;
