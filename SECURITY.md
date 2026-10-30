# Security

## Supported versions

Fixes go into the latest release only. `s3tx`, `tx` and the mediator are released
together, under one version.

## Reporting a vulnerability

Report it privately, through GitHub:
[Security > Report a vulnerability](https://github.com/junior/s3tx/security/advisories/new).
Please do not open a public issue for it.

Describe what you found and how to reproduce it, but never include anything live: no
working pre-signed URL, short code, client id, admin token or AWS key. A redacted example
is enough.

This is a personal project, answered as time allows.

## Scope

- `s3tx`, the script on the trusted machine.
- `tx`, the far-end client.
- The mediator in [`mediator/`](mediator/).

How they keep the bucket private, and what a code or a link grants, is in the README's
[Security model](README.md#security-model) and in [`mediator/README.md`](mediator/README.md).
A bucket's own configuration is its owner's: `s3tx status` and `s3tx doctor` check it.
