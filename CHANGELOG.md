# Changelog

Notable changes to s3tx, tx and the mediator. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public interface is
the s3tx command line (commands, flags, environment variables) and the protocol
between tx and the mediator.

## [Unreleased]

## [1.5.0] - 2026-10-29

### Added

- `s3tx doctor` checks the setup end to end, one line per check: the versions, the config
  file, the credentials, the bucket and its Block Public Access, the mediator and the
  admin token (with an empty request that registers nothing), the clock against the
  servers', and the `tx` on this machine. It exits 1 when a check fails.
- `s3tx send` records the file's SHA-256 with the object, and `tx get` checks the
  download against it with `sha256sum`, `shasum` or `openssl`. A download that does not
  match is kept aside, never saved under its name.
- `tx version` (and `tx --version`) prints tx's version, which is s3tx's.
- zsh completion for s3tx, in `completions/_s3tx`; the Homebrew formula installs it.
- A workflow publishes each release: the GitHub Release takes its notes from this file
  and carries `s3tx`, `tx` and their `SHA256SUMS`, with build-provenance attestations;
  the Homebrew formula follows once the release is out.
- `SECURITY.md`: how to report a vulnerability.

### Changed

- s3tx needs Python 3.11 or later (3.10 reaches its end of life this month); uv fetches
  one when the machine has none. CI tests 3.11 and 3.15.
- s3tx asks for boto3 1.36 or later, the first with the checksum settings that
  S3-compatible servers need.
- `tx get` saves a file with the mode the umask gives a new file, as `curl -o` does,
  instead of 0600.
- The mediator runs on Node.js 26, pinned in its `package.json`, with its dependencies
  locked in `package-lock.json`; CI fails on any `npm audit` finding.

### Deprecated

- The built-in `S3TX_BUCKET` (`s3tx`). Set the bucket in the config file or the
  environment: s3tx warns until it is set, and 2.0 drops the default.

### Fixed

- `tx get` falls back to the manifest when the mediator does not know a code, so a
  manifest code works on a machine that also has a mediator.

### Security

- Every mediator answer carries `Cache-Control: no-store`, so no proxy on the way keeps a
  pre-signed URL.

## [1.4.0] - 2026-10-26

### Added

- `tx get <code> <folder>` saves the file into an existing folder under its own name (the
  last part of its URL), as `tx get <code>` does in the current folder.

### Changed

- `tx get` checks that its target folder exists and can be written before it resolves a
  code, so a mistyped folder no longer spends a single-use code.

## [1.3.0] - 2026-10-14

### Added

- Profiles. `~/.config/s3tx/config` and `~/.config/tx/config` can hold named profiles in
  `[name]` sections, chosen with `--profile NAME` on either tool; the lines before any
  section are the default profile, so existing files keep working. A named profile uses
  only its own section, never the environment or `[default]`; the environment overrides
  the default profile only.
- `s3tx config` and `tx config` show the settings in use and where each one comes from,
  with secrets masked.
- S3-compatible servers: `S3TX_ENDPOINT_URL` and `S3TX_CA_BUNDLE`. With a server other
  than AWS, s3tx uses path-style addresses, sends checksums only where the S3 API requires
  one, and skips the AWS-only identity and Block Public Access checks; `lockdown` refuses.
- `S3TX_AWS_PROFILE` names the AWS credentials profile; `S3TX_PROFILE` still works.
- Both tools warn when their config file is readable by other users.

### Changed

- tx reads `~/.config/tx/config` instead of running it as shell code. `KEY=VALUE` lines,
  comments and quotes work as before; shell commands or variable references there no
  longer do.

### Fixed

- tx no longer ignores its config file when the environment sets only one of
  `TX_MEDIATOR` and `TX_CLIENT_ID`: whatever the environment leaves out comes from the
  file.

## [1.2.0] - 2026-10-13

### Changed

- On AWS, s3tx signs links for the region the bucket really lives in, whatever
  `S3TX_REGION` says, and `status` shows that region. Any other endpoint, such as an
  S3-compatible service set through `AWS_ENDPOINT_URL`, keeps `S3TX_REGION` as
  configured.
- s3tx needs Python 3.10 or later, as current boto3 does. uv fetches a suitable Python
  by itself when the machine has none.
- s3tx's help and messages, and tx's messages and comments, use plain ASCII punctuation;
  what they do is unchanged.
- The README installs s3tx and tx from a release tag, not from main.

## [1.1.0] - 2026-10-13

### Added

- tx and the mediator live in this repository, with a README, an architecture diagram,
  the MIT licence, lint and CI.
- The mediator merges `CLIENTS_EXTRA` over `CLIENTS`, so a machine can be added without
  reading back `CLIENTS`, which Vercel stores write-only.
- The mediator accepts only `https` URLs on AWS hosts, or exactly `ALLOWED_URL_HOST`
  when that is set.
- Tests for every s3tx command, for tx under `sh` and `dash`, for full round trips and
  for the mediator handlers, all against local stand-ins: no AWS needed.
- `s3tx --version`, which needs no credentials or dependencies.

### Changed

- `--expires` is capped at 7 days, the most a SigV4 link can live.
- A non-numeric `S3TX_EXPIRES` falls back to 600 seconds with a warning.
- A code that collides with a live one at the mediator is regenerated instead of
  failing.
- The mediator creates its per-client rate-limit counter together with its expiry, so
  an interrupted request can no longer lock a client out.
- `s3tx rm` of a key that is not there fails with "object not found" (exit 1) instead of
  reporting a deletion. `rm` and `link` report any other lookup error, such as a refused
  permission, as itself rather than as a missing object.

### Fixed

- s3tx signs its presigned URLs with SigV4. Until now they were SigV2, boto3's default
  for S3 presigning, while the 7-day cap and the docs assumed SigV4. Links keep the
  same host, so the mediator's URL check is unaffected. With SigV4, `S3TX_REGION` must
  be the bucket's region.
- With `--to`, s3tx reports how long the code really lives, as the mediator granted it
  (at most 900 seconds whatever `--expires` asked), and the clock time it dies.
- The mediator keeps a code single-use when several redemptions of it race: only the
  one whose delete removes the code gets the URL, the others get 404. Before, each of
  them could read the code before any had deleted it, and all got the URL.

### Security

- Short codes come from the `secrets` module instead of `random`.

## [1.0.0] - 2026-06-18

- s3tx moves files in and out through a private S3 bucket and presigned URLs: `send`,
  `link`, `drop`, `recv`, `ls`, `rm`, `rmrf`, `status`, `lockdown`.
- Short codes instead of long URLs: `--code` through a private manifest in the bucket
  (`manifest-url`, `codes`), or `--to` through the mediator, single use and scoped to
  one client.
- A dedicated AWS profile (`S3TX_PROFILE`, default `s3tx`) and a least-privilege IAM
  policy.
- `send -` reads stdin.

[Unreleased]: https://github.com/junior/s3tx/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/junior/s3tx/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/junior/s3tx/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/junior/s3tx/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/junior/s3tx/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/junior/s3tx/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/junior/s3tx/releases/tag/v1.0.0
