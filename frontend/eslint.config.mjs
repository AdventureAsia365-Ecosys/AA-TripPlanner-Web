// AA-904 — ESLint 9 flat config. eslint-config-next 15 still ships eslintrc-style presets, so they
// are loaded through FlatCompat (the pattern the Next 15 docs give). Same rule set as the old
// .eslintrc.json ("next/core-web-vitals"); `npm run lint` calls the ESLint CLI directly because
// `next lint` is deprecated in Next 15.5 and removed in Next 16.
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const eslintConfig = [
  { ignores: [".next/**", ".vercel/**", "out/**", "build/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals"),
];

export default eslintConfig;
