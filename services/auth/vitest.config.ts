import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
    globals: false,
    testTimeout: 5000,
    // TypeORM entity decorators (on the domain entities) need reflect-metadata
    // loaded before any entity module is imported.
    setupFiles: ["reflect-metadata"],
  },
});
