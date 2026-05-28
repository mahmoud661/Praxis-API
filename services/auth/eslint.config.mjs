// ESLint 9+ flat config. Kept light: lint syntax + TS rules, no type-aware
// checks (those need the typechecker and would slow the pre-commit hook).
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**"],
  },
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
    },
    rules: {
      // Allow intentionally-unused names by underscoring them.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-explicit-any": "warn",
      // We pass DI tokens by Symbol/string; not every constructor param is a class.
      "@typescript-eslint/no-empty-object-type": "off",
    },
  },
);
