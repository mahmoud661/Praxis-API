import { defineConfig } from "vitest/config";

// Integration tests use testcontainers to spin up a real Postgres. They are
// NOT part of `npm test` (which runs inside the Docker image build and must
// be hermetic). Run via `npm run test:integration` on a machine with Docker.
//
// The globalSetup file owns the container lifecycle: it starts a fresh
// `postgres:16-alpine` once per worker pool, points DATABASE_URL at it,
// and stops it during teardown. Test files then import data-source as
// normal — by the time their module bodies execute, `loadEnv()` sees
// the testcontainer's URL.
export default defineConfig({
  test: {
    include: ["tests-integration/**/*.test.ts"],
    environment: "node",
    globals: false,
    globalSetup: ["./tests-integration/global-setup.ts"],
    // testcontainers can take time on first run while images pull.
    testTimeout: 120_000,
    hookTimeout: 120_000,
    // Run the file serially — every test truncates the same tables, so
    // parallel runs would step on each other.
    fileParallelism: false,
  },
});
