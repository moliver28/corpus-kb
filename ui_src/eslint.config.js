import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// UI SPA hygiene (zero-drift amendment 2): TypeScript strict with no
// `any` and no `@ts-ignore` — the mirror of the Python no-`type: ignore`
// rule. Both are hard errors here so `poe ui-lint` fails on them.
export default tseslint.config(
  { ignores: ["node_modules", "../src/corpus_kb/ui_dist"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/ban-ts-comment": "error",
    },
  },
);
