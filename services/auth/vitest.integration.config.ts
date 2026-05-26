import { defineConfig } from "vitest/config";

// Integration tests use testcontainers to spin up a real Postgres. They are
// NOT part of `npm test` (which runs inside the Docker image build and must
// be hermetic). Run via `npm run test:integration` on a machine with Docker.
export default defineConfig({
  test: {
    include: ["tests-integration/**/*.test.ts"],
    environment: "node",
    globals: false,
    // testcontainers can take time on first run while images pull.
    testTimeout: 120_000,
    hookTimeout: 120_000,
  },
});
