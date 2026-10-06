# Changelog

Notable changes to s3tx, tx and the mediator. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The public interface is
the s3tx command line (commands, flags, environment variables) and the protocol
between tx and the mediator.

## [Unreleased]

### Added

- tx and the mediator live in this repository, with a README, an architecture diagram,
  the MIT licence, lint and CI.
- The mediator merges `CLIENTS_EXTRA` over `CLIENTS`, so a machine can be added without
  reading back `CLIENTS`, which Vercel stores write-only.
- The mediator accepts only `https` URLs on AWS hosts, or exactly `ALLOWED_URL_HOST`
  when that is set.
- Tests for every s3tx command, for tx under `sh` and `dash`, for full round trips and
  for the mediator handlers, all against local stand-ins: no AWS needed.

### Changed

- `--expires` is capped at 7 days, the most a SigV4 link can live.
- A non-numeric `S3TX_EXPIRES` falls back to 600 seconds with a warning.
- A code that collides with a live one at the mediator is regenerated instead of
  failing.
- The mediator creates its per-client rate-limit counter together with its expiry, so
  an interrupted request can no longer lock a client out.

### Fixed

- s3tx signs its presigned URLs with SigV4. Until now they were SigV2, boto3's default
  for S3 presigning, while the 7-day cap and the docs assumed SigV4. Links keep the
  same host, so the mediator's URL check is unaffected. With SigV4, `S3TX_REGION` must
  be the bucket's region.
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

[Unreleased]: https://github.com/junior/s3tx/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/junior/s3tx/releases/tag/v1.0.0
