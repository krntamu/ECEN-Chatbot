// Plain JS config so Jest needs no ts-node to parse it (a .ts config requires
// ts-node, which isn't a declared dependency — see issue #15 post-mortem).
const nextJest = require("next/jest.js");

const createJestConfig = nextJest({ dir: "./" });

/** @type {import('jest').Config} */
const config = {
  testEnvironment: "jest-environment-jsdom",
  // NOTE: the option is `setupFilesAfterEnv`, NOT `setupFilesAfterFramework`.
  // The wrong key silently skips jest.setup.ts, so @testing-library/jest-dom
  // matchers (toBeDisabled, toBeInTheDocument, ...) never load and any
  // component test fails. This was the root cause that blocked the #15 fix.
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
  // A local `next build` leaves a second package.json under .next/standalone;
  // without this Jest scans it (duplicate-module warnings, and a hard read
  // error on some filesystems).
  modulePathIgnorePatterns: ["<rootDir>/.next/"],
};

// react-markdown and everything under it (unified, remark, micromark, ...)
// ship as ES modules only, which Jest can't load untransformed. next/jest sets
// transformIgnorePatterns itself and ignores a value passed in above, so it is
// patched after the config is built. Needed by tests that render real markdown
// (__tests__/adversarial.test.tsx).
const ESM_PACKAGES = [
  "react-markdown", "unified", "bail", "is-plain-obj", "trough", "devlop",
  "vfile", "vfile-message", "unist-.*", "mdast-.*", "hast-.*", "micromark.*",
  "remark-.*", "decode-named-character-reference", "character-entities.*",
  "character-reference-invalid", "is-(alphabetical|alphanumerical|decimal|hexadecimal)",
  "parse-entities", "stringify-entities", "property-information",
  "space-separated-tokens", "comma-separated-tokens", "html-url-attributes",
  "estree-util-.*", "style-to-(object|js)", "inline-style-parser",
  "trim-lines", "zwitch", "ccount", "longest-streak", "markdown-table",
];

module.exports = async () => {
  const built = await createJestConfig(config)();
  built.transformIgnorePatterns = [
    `/node_modules/(?!(${ESM_PACKAGES.join("|")})/)`,
    "^.+\\.module\\.(css|sass|scss)$",
  ];
  return built;
};
