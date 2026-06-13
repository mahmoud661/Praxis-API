#!/usr/bin/env node
// Event-contract validator. Run from the repo root (or anywhere):
//
//   node scripts/validate-contracts.mjs
//
// Zero dependencies — plain Node. CI runs this as the `contracts` job so a
// PR can't land a topic without a schema (or a schema nobody registered).
//
// What this script checks (conventions live in contracts/README.md):
//   1. contracts/topics.json parses, and every topic declares
//      `name`, `partitions`, `replicationFactor`, and `events`.
//   2. Topic names follow `<bounded-context>.<event-class>.v<major>`;
//      event names are PascalCase.
//   3. Every listed event has a schema file at
//      contracts/schemas/<topic>/<EventName>.json that is valid JSON,
//      declares `type: "object"`, has a non-empty `properties` map, and
//      describes the *payload only* — the envelope (`metadata` + `payload`
//      wrapper) is shared platform shape, so a schema that redefines
//      `metadata`/`payload` keys is a mistake.
//   4. No orphan schema files: everything on disk under contracts/schemas/
//      must be reachable from topics.json.
//
// Exit code: 0 when clean (prints a one-line summary), 1 with one line per
// problem otherwise.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOPICS_FILE = path.join(ROOT, "contracts", "topics.json");
const SCHEMAS_DIR = path.join(ROOT, "contracts", "schemas");

// ---- output helpers ---------------------------------------------------
// ANSI only when stdout is a TTY (CI / piped runs stay readable).
const tty = process.stdout.isTTY;
const paint = (code, s) => (tty ? `\x1b[${code}m${s}\x1b[0m` : s);
const ok = (s) => console.log(`${paint(32, "✓")} ${s}`);
const err = (s) => console.error(`${paint(31, "✗")} ${s}`);
const rel = (p) => path.relative(ROOT, p).split(path.sep).join("/");

const errors = [];
const fail = (msg) => errors.push(msg);

// ---- conventions (contracts/README.md) --------------------------------
const TOPIC_NAME_RE = /^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\.v[1-9]\d*$/; // auth.events.v1
const EVENT_NAME_RE = /^[A-Z][A-Za-z0-9]*$/; // UserRegistered
const ENVELOPE_KEYS = ["metadata", "payload"]; // shared wrapper, not per-event

// ---- 1) parse topics.json ---------------------------------------------
let registry;
try {
  registry = JSON.parse(readFileSync(TOPICS_FILE, "utf8"));
} catch (e) {
  err(`${rel(TOPICS_FILE)}: ${e.message}`);
  process.exit(1);
}
if (!Array.isArray(registry?.topics)) {
  err(`${rel(TOPICS_FILE)}: expected a top-level "topics" array`);
  process.exit(1);
}

// ---- 2) validate each topic + its schemas ------------------------------
// Map of every schema file the registry claims, so the orphan scan below
// can tell "registered" from "left behind after a rename".
const claimed = new Set();
const seenTopics = new Set();
let schemasChecked = 0;

for (const topic of registry.topics) {
  const label = `${rel(TOPICS_FILE)} → topic "${topic?.name ?? "?"}"`;

  if (typeof topic?.name !== "string" || !TOPIC_NAME_RE.test(topic.name)) {
    fail(`${label}: name must match <bounded-context>.<event-class>.v<major> (e.g. auth.events.v1)`);
    continue; // every later check keys off the name
  }
  if (seenTopics.has(topic.name)) {
    fail(`${label}: duplicate topic entry`);
    continue;
  }
  seenTopics.add(topic.name);

  if (!Number.isInteger(topic.partitions) || topic.partitions < 1) {
    fail(`${label}: "partitions" must be a positive integer`);
  }
  if (!Number.isInteger(topic.replicationFactor) || topic.replicationFactor < 1) {
    fail(`${label}: "replicationFactor" must be a positive integer`);
  }
  if (!Array.isArray(topic.events)) {
    fail(`${label}: "events" must be an array (use [] for none yet)`);
    continue;
  }

  for (const event of topic.events) {
    if (typeof event !== "string" || !EVENT_NAME_RE.test(event)) {
      fail(`${label}: event "${event}" must be PascalCase (e.g. UserRegistered)`);
      continue;
    }
    const schemaFile = path.join(SCHEMAS_DIR, topic.name, `${event}.json`);
    claimed.add(schemaFile);

    if (!existsSync(schemaFile)) {
      fail(`${label}: event "${event}" has no schema — expected ${rel(schemaFile)}`);
      continue;
    }

    let schema;
    try {
      schema = JSON.parse(readFileSync(schemaFile, "utf8"));
    } catch (e) {
      fail(`${rel(schemaFile)}: ${e.message}`);
      continue;
    }

    // Structural sanity: payload schemas are plain JSON Schema objects.
    if (schema.type !== "object") {
      fail(`${rel(schemaFile)}: "type" must be "object" (payloads are JSON objects)`);
    }
    if (typeof schema.properties !== "object" || schema.properties === null || Object.keys(schema.properties).length === 0) {
      fail(`${rel(schemaFile)}: must declare a non-empty "properties" map`);
    } else {
      // Schemas describe the payload only — the metadata/payload envelope
      // is the platform's job (see contracts/README.md "Envelope").
      for (const key of ENVELOPE_KEYS) {
        if (key in schema.properties) {
          fail(`${rel(schemaFile)}: declares "${key}" — schemas describe the payload only, the envelope is added by the platform`);
        }
      }
      // `required` fields must actually exist in properties.
      if (schema.required !== undefined) {
        if (!Array.isArray(schema.required)) {
          fail(`${rel(schemaFile)}: "required" must be an array`);
        } else {
          for (const field of schema.required) {
            if (!(field in schema.properties)) {
              fail(`${rel(schemaFile)}: required field "${field}" missing from "properties"`);
            }
          }
        }
      }
    }
    if (schema.title !== undefined && schema.title !== event) {
      fail(`${rel(schemaFile)}: "title" is "${schema.title}" but the file/event name is "${event}"`);
    }
    schemasChecked++;
  }
}

// ---- 3) orphan scan -----------------------------------------------------
// A schema on disk that topics.json doesn't list is a contract nobody can
// discover — usually a leftover from a rename or a forgotten registration.
if (existsSync(SCHEMAS_DIR)) {
  for (const dir of readdirSync(SCHEMAS_DIR)) {
    const topicDir = path.join(SCHEMAS_DIR, dir);
    if (!statSync(topicDir).isDirectory()) {
      fail(`${rel(topicDir)}: unexpected file — schemas live at schemas/<topic>/<EventName>.json`);
      continue;
    }
    if (!seenTopics.has(dir)) {
      fail(`${rel(topicDir)}/: topic "${dir}" is not in ${rel(TOPICS_FILE)}`);
      continue;
    }
    for (const file of readdirSync(topicDir)) {
      const schemaFile = path.join(topicDir, file);
      if (!file.endsWith(".json")) {
        fail(`${rel(schemaFile)}: unexpected file — only <EventName>.json belongs here`);
      } else if (!claimed.has(schemaFile)) {
        fail(`${rel(schemaFile)}: orphan schema — "${file.replace(/\.json$/, "")}" is not listed under "${dir}" in ${rel(TOPICS_FILE)}`);
      }
    }
  }
}

// ---- result -------------------------------------------------------------
if (errors.length > 0) {
  for (const e of errors) err(e);
  err(`${errors.length} contract problem${errors.length === 1 ? "" : "s"} found`);
  process.exit(1);
}
ok(`contracts OK — ${seenTopics.size} topic${seenTopics.size === 1 ? "" : "s"}, ${schemasChecked} event schema${schemasChecked === 1 ? "" : "s"} validated`);
