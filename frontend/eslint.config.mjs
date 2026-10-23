// AA-906 — ESLint 9 native flat config. eslint-config-next 16 ships flat presets, so the
// FlatCompat bridge of AA-904 (and the @eslint/eslintrc dependency) is gone. Same preset as before
// (`core-web-vitals`), which in Next 16 also carries the React Compiler rules of
// eslint-plugin-react-hooks 7 (refs, set-state-in-effect, immutability, purity …) as errors.
// `npm run lint` calls the ESLint CLI directly (`next lint` was removed in Next 16).
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = defineConfig([
  ...nextVitals,
  globalIgnores([".next/**", ".vercel/**", "out/**", "build/**", "next-env.d.ts"]),
]);

export default eslintConfig;
