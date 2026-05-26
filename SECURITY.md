# Security policy

Thanks for taking the time to report a security issue. The backend
handles authentication, sessions, and AI agent execution — that's a
high-trust surface, and we take vulnerabilities seriously.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security problem.** Use one of
the private channels below instead, in order of preference:

1. **GitHub Security Advisories** (preferred)
   Go to the repo's **Security** tab → **Report a vulnerability**. This
   creates a private discussion only maintainers can see, and lets us
   issue a CVE if the bug warrants one.
2. **Email** the maintainer listed in `.github/OWNERS` directly. Use a
   subject line starting with `[security]`.

Please include:

- A clear description of the issue and its impact.
- Step-by-step reproduction (a curl command + the request body is ideal).
- The commit SHA or image tag where you observed it.
- Whether you've shared the details with anyone else.

We acknowledge reports within **72 hours** and aim to ship a fix or a
mitigation within **14 days** for high-severity issues. We'll credit you
in the advisory unless you ask us not to.

## Coordinated disclosure

Please give us a reasonable window to ship a fix before publishing.
Standard practice for this repo:

- **Critical / high** (auth bypass, RCE, data exfiltration): 30 days or
  until a fix is released, whichever is sooner.
- **Medium / low**: 60 days.

If you don't hear back within 7 days, send a follow-up — emails get lost.

## In scope

Anything inside this repository, including:

- Gateway HTTP endpoints (`backend/gateway/`).
- Auth service: signup, login, sessions, password handling, cookies
  (`backend/services/auth/`).
- AI agents service: prompt handling, tool execution, model invocation
  (`backend/services/ai-agents/`).
- The Kafka event surface and outbox publisher.
- Database schemas and migrations (any privilege-escalation or
  injection vector).
- Docker / compose configuration shipped here (`backend/infra/`).
- CI/CD workflows (`.github/workflows/`) for supply-chain risks.

## Out of scope

- Issues in third-party dependencies — please report those to the
  upstream project. If a dep has a published CVE that affects us, that's
  in scope: we want to know which version + which call site.
- Social engineering, physical attacks, or anything requiring a
  compromised maintainer account.
- Self-XSS or attacks requiring the victim to paste arbitrary code into
  their own browser console.
- Findings that require a non-default configuration (e.g. running the
  gateway without HTTPS in production).
- Volumetric DoS — open a regular issue if you've found a cheap path
  to amplification, but pure "send a million requests" doesn't qualify.

## Hall of fame

Reporters who follow this process get credited in the published
advisory and in our `CHANGELOG.md` security section.
