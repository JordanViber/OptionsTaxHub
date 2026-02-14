module.exports = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json", "node"],
  testMatch: ["**/tests/unit/**/*.(spec|test).[tj]s?(x)"],
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          jsx: "react-jsx",
        },
      },
    ],
  },
  collectCoverageFrom: [
    "app/**/*.{ts,tsx}",
    "lib/**/*.{ts,tsx}",
    "public/**/*.js",
    "!app/**/*.d.ts",
    "!lib/**/*.d.ts",
    "!app/**/layout.tsx",
    "!app/**/layout-client.tsx",
    "!app/hooks/usePushNotifications.ts",
    "!lib/queryClient.ts",
    "!lib/theme.ts",
    "!public/sw.js",
  ],
  coveragePathIgnorePatterns: [
    "/node_modules/",
    "/tests/",
    "\\.test\\.",
    "\\.spec\\.",
  ],
};
