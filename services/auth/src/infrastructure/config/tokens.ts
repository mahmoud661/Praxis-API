// Tokens for infrastructure resources that are third-party clients (no class
// of ours to use as a token, and not a repo/service the DI scan can name).
export const REDIS_TOKEN = Symbol("Redis");
export const DATA_SOURCE_TOKEN = Symbol("DataSource");
