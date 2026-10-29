// AA-906 — ESLint 9 native flat config. eslint-config-next 16 ships flat presets, so the
// FlatCompat bridge of AA-904 (and the @eslint/eslintrc dependency) is gone. Same preset as before
// (`core-web-vitals`), which in Next 16 also carries the React Compiler rules of
// eslint-plugin-react-hooks 7 (refs, set-state-in-effect, immutability, purity …) as errors.
// `npm run lint` calls the ESLint CLI directly (`next lint` was removed in Next 16).
//
// AA-994 S5a (root ADR 0016 decision 7) — the UI kit conformance rules, vendored with the kit core tokens profile
// (ui/core/eslint-kit.mjs, byte-identical in every app), `tailwind` preset at `error`, only the token rules:
//   no-hex-colour (4), no-raw-aa-var (6), no-aa-var-definition (7).
// The others are off: TripPlanner has the tokens profile only, so there is no PageFrame, DataTable, Drawer/Modal or kit
// Button to send a page to (no-raw-main, no-raw-table, no-raw-dialog, no-hand-styled-button: plan guards G1-G3, G5),
// and no legacy kit module exists here (no-legacy-kit).
// Scope: page code (app/**) and the app's components (components/**, ui/** minus the kit core copy).
// Violations that existed before S5a are frozen in eslint-suppressions.json (ESLint bulk suppressions); the Frontend job
// prunes it and fails when it is stale or grows (check-kit-copy.mjs --suppressions).
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import { kitConfig } from "./ui/core/eslint-kit.mjs";

const eslintConfig = defineConfig([
  ...nextVitals,
  globalIgnores([".next/**", ".vercel/**", "out/**", "build/**", "next-env.d.ts"]),
  kitConfig({
    preset: "tailwind",
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "ui/**/*.{ts,tsx}"],
    ignores: ["ui/core/**"],
    rules: {
      "aa-kit/no-raw-main": "off",
      "aa-kit/no-raw-table": "off",
      "aa-kit/no-raw-dialog": "off",
      "aa-kit/no-hand-styled-button": "off",
      "aa-kit/no-legacy-kit": "off",
    },
  }),
]);

export default eslintConfig;
