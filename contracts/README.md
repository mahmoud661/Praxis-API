# Event contracts

Source of truth for Kafka topics and their event schemas. Polyglot-safe:
schemas live as plain JSON Schema so any service (TS, Python, Go...) can
generate types or validate at runtime.

## Naming

- **Topic:** `<bounded-context>.<event-class>.v<major>`
  - `auth.events.v1`, `agents.events.v1`
- **Event name** (in the `event-name` header): PascalCase
  - `UserRegistered`, `AgentRunCompleted`
- Bump the topic suffix (`.v2`) for breaking changes. Additive changes
  (new optional field) stay on the same version.

## Envelope

Every message put on a platform topic uses this shape:

```json
{
  "metadata": {
    "eventId":     "uuid",
    "occurredAt":  "ISO-8601",
    "eventName":   "PascalCase",
    "aggregateId": "natural key, e.g. userId",
    "version":     1
  },
  "payload": { /* event-specific, see schemas/ */ }
}
```

Kafka record:

- **key**     = `metadata.aggregateId` (ordering within an aggregate)
- **headers** = `event-name`, `event-id`, `event-version`

## Topic registry

| Topic              | Producers   | Consumers          | Notes              |
| ------------------ | ----------- | ------------------ | ------------------ |
| `auth.events.v1`   | auth        | ai-agents          | User lifecycle     |
| `agents.events.v1` | ai-agents   | (future services)  | Agent run results  |

## Schemas

JSON Schema files under `schemas/<topic>/<EventName>.json`.

CI enforces the structure via `scripts/validate-contracts.mjs` (the
`contracts` job): every event listed in `topics.json` must have a schema
file here, every schema must be a sane payload schema (`type: "object"`
with `properties`, no envelope keys), and orphan schema files fail the
build. Run it locally with `node scripts/validate-contracts.mjs`.

Runtime payload validation against these schemas is still up to each
producer/consumer.
