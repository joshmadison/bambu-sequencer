# Security policy

## Supported versions

Only the latest commit on the `master` branch is supported. There are no
backports for older releases.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting:

<https://github.com/joshmadison/plateloop/security/advisories/new>

Include:

- the affected endpoint or deployment component
- a minimal reproduction (request, file type, configuration)
- the impact you observed
- the version or commit hash

Do not attach real `.3mf` files, personal data, or credentials unless they are
strictly necessary. A synthetic reproduction is preferred.

## Scope

Useful reports include upload/parser crashes, memory exhaustion, bypasses of the
rate or size limits, authentication or reverse-proxy issues, and container
escape risks. The app intentionally has no accounts and does not persist
uploaded files.

## Operational expectations

This is a hobby project without a bug bounty. Reports are handled on a
best-effort basis. If you run a public instance, keep dependencies and the
Docker base image updated, and follow `docs/DEPLOY.md`.
