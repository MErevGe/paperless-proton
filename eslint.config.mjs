import globals from "globals";

export default [
  {
    files: ["bridge_container/static/*.js"],
    languageOptions: {
      globals: globals.browser,
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: {
      "no-unused-vars": "error",
      "no-undef": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      eqeqeq: "error",
    },
  },
  {
    files: ["tests/*.mjs", "eslint.config.mjs"],
    languageOptions: {
      globals: globals.node,
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: { "no-unused-vars": "error", "no-undef": "error", eqeqeq: "error" },
  },
];
