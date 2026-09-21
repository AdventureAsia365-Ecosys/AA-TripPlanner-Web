import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
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
