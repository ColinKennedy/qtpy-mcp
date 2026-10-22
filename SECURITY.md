# Security Policy

## Supported Versions

Only the latest released version is supported with security fixes.

## Reporting a Vulnerability

Please do not open a public issue for security vulnerabilities.

Use GitHub's private vulnerability reporting flow:

- the repository's **Security → Report a vulnerability** page

Include:

- A clear description of the issue and impact
- Steps to reproduce
- Any proof-of-concept details
- Suggested mitigations (if known)

You will receive acknowledgement as quickly as possible, followed by triage,
fix development, and coordinated disclosure.

## Threat model notes

The probe listens on `127.0.0.1` only, on an ephemeral port, and requires a per-process
random token that is stored in a registry file readable only by your user. It can click,
type, set properties and call slots in the host application, so treat anything that can
read your registry directory as having control of those applications.
