# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. (Maintainers: enable **Settings → Code security → Private vulnerability reporting** after publishing.) Use GitHub's private vulnerability reporting
(**Security → Report a vulnerability** on this repository). Include:
- the affected version or commit
- steps or an input file that reproduces it (synthetic data only, never real PHI or production EDI)
- the impact you observed

You'll get an acknowledgment within a few days. Fixes are released as a new image tag and noted in
[CHANGELOG.md](CHANGELOG.md).

## Scope

The parser, CLI, HTTP service, container image and deployment manifests in this repository. The service has no
built-in authentication by design; deploying it without a gateway is a configuration issue, not a vulnerability.
See [docs/security.md](docs/security.md) for the threat model and hardening checklist.
