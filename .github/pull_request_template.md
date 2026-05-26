## Summary
<!-- One short paragraph: what changes and why. Skip the "what" of the diff
     itself — focus on why this matters and the user-facing effect. -->

## Type of change

<!-- Pick one. Delete the others. -->

- [ ] feat: new functionality
- [ ] fix: bug fix
- [ ] refactor: behavior unchanged, code organization improved
- [ ] infra: docker-compose, CI/CD, observability config
- [ ] docs: README / CONTRIBUTING / ARCHITECTURE only
- [ ] chore: dependency bumps, formatting, repo plumbing

## Touched area

<!-- The PR labeler will tag automatically; this is for human readers. -->

- [ ] `gateway/`
- [ ] `services/auth/`
- [ ] `services/ai-agents/`
- [ ] `infra/`
- [ ] `contracts/`
- [ ] migrations
- [ ] CI/CD

## Related issues
<!-- "closes #123" auto-closes the issue on merge. -->

-

## Checklist

- [ ] Unit tests added or updated (they run inside the Dockerfile so a
      green `ci` workflow means the image already passes them)
- [ ] If I added/changed a Kafka event, I updated `backend/contracts/`
- [ ] If I changed a DB schema, I added a new migration file
      (no edits to existing migrations)
- [ ] If I added a new env var, I updated `backend/.env.example`
- [ ] I ran the local smoke test from `backend/README.md` (or explained
      below why I couldn't)

## Local verification

<!-- Paste the commands you ran + a short note on what you observed.
     "make up && curl -k https://localhost:8443/v1/auth/me … saw 200" is
     ample. -->

```
```

## Notes for reviewers
<!-- Anything subtle: trade-offs, alternatives considered, things you're
     unsure about. -->
