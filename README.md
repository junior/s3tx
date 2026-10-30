# s3tx

> Move files in and out of a locked-down environment through a **private** S3 bucket,
> using time-limited pre-signed URLs. The far end needs nothing but `curl`.

[![release](https://img.shields.io/github/v/release/junior/s3tx)](https://github.com/junior/s3tx/releases/latest)
![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![install: single file](https://img.shields.io/badge/install-single%20file-success.svg)
![lint: ruff](https://img.shields.io/badge/lint-ruff-261230.svg)
[![CI](https://github.com/junior/s3tx/actions/workflows/ci.yml/badge.svg)](https://github.com/junior/s3tx/actions/workflows/ci.yml)

`s3tx` runs on a trusted machine (your Mac) that holds AWS credentials. It uploads to a
bucket that is **never public**, then hands the other end a short-lived signed link, or
an even shorter *code*. The far end (a work laptop behind a corporate proxy, a phone, a
Raspberry Pi) downloads or uploads with plain `curl`: no AWS account, no CLI, no creds.

![s3tx architecture](docs/architecture.svg)

## Why

- **The bucket is never public.** Block Public Access stays fully on. Access is granted
  per-object, time-boxed, by a signed URL, not by a bucket policy.
- **Zero credentials on the far end.** It only ever sees a URL or a short code.
- **Least privilege on the trusted side.** `s3tx` uses a dedicated AWS profile bound to a
  narrow IAM user (see [`iam-policy.json`](iam-policy.json)), never your shell default.
- **One file, no install dance.** A single script with [PEP 723](https://peps.python.org/pep-0723/)
  inline dependencies; [uv](https://docs.astral.sh/uv/) fetches `boto3` on first run.

## Install (the trusted machine)

On a Mac with Homebrew: `brew install junior/tap/s3tx` (the formula follows every
release, and brings zsh completion). Anywhere else, `s3tx` is one file. Take it from a
release tag, so you know which version you run:

```sh
curl -fsSL https://raw.githubusercontent.com/junior/s3tx/v1.4.0/s3tx -o ~/.local/bin/s3tx
chmod +x ~/.local/bin/s3tx        # make sure ~/.local/bin is on your $PATH
s3tx --version
```

<sub>It needs [uv](https://docs.astral.sh/uv/): the shebang runs `uv run --script`, so the
first invocation provisions Python and `boto3` by itself, with no virtualenv and no
`pip install`. Supported: Python 3.11 or later. It still runs on 3.9 and 3.10, which are
past their end of life (CI checks 3.9). For zsh completion without Homebrew, copy
[`completions/_s3tx`](completions/_s3tx) into a directory on your `$fpath`. To follow
development instead, clone the repository and link `s3tx` onto your `$PATH`.</sub>

<sub>From 1.5.0 on, every [release](https://github.com/junior/s3tx/releases) attaches
`s3tx`, `tx` and their `SHA256SUMS`, attested by the workflow that published them, and is
immutable: `gh release verify vX.Y.Z --repo junior/s3tx` checks a release, and
`gh attestation verify tx --repo junior/s3tx` a downloaded file.</sub>

One-time AWS setup: a private bucket (Block Public Access on) and an IAM user with the
policy in [`iam-policy.json`](iam-policy.json), wired to a dedicated profile:

```sh
aws configure --profile s3tx
s3tx status        # confirm the identity + that the bucket is private
s3tx lockdown      # force Block-Public-Access on, drop any bucket policy
s3tx doctor        # check it all: credentials, bucket, mediator, clock, tx
```

Name your bucket with `S3TX_BUCKET` (see [Configuration](#configuration)): the built-in
default, `s3tx`, is deprecated and goes away in 2.0.

## Quickstart

**Send a file out.** The other end downloads it:

```sh
s3tx send report.pdf
#  -> prints a time-limited download link
# on the far end:
curl -L -O "<that link>"
```

**Receive a file in.** The other end uploads, with no credentials:

```sh
s3tx drop inbox/photo.jpg
#  -> prints a time-limited upload (PUT) link
# on the far end:
curl -X PUT --upload-file photo.jpg "<that link>"
# then pull it here:
s3tx recv inbox/photo.jpg
```

## Short codes

Pasting a 500-character URL is miserable. `s3tx` can hand the far end a short code instead,
redeemed by the bundled [`tx`](tx) client (install + config below):

| Flag | Mechanism | Far end needs | Best for |
|------|-----------|---------------|----------|
| `--to CLIENT` | [mediator](mediator/) (Vercel + Redis) | its `TX_CLIENT_ID` | single-use, **scoped** to one machine |
| `--code [CODE]` | a private manifest object in the bucket | the manifest seed URL | no third-party service |
| *(neither)* | the raw signed URL | nothing | one-off, nothing to set up |

```sh
s3tx send report.pdf --to laptop      # -> "tx get a3Kf9p2Q" on that machine
```

The mediator path is the strongest: each code is single-use, expires, is scoped to exactly
one client, and brute-force is rate-limited. See [`mediator/`](mediator/) to deploy it.

## The far end (`tx`)

The companion client is [`tx`](tx), a single POSIX-shell script that needs only `curl`
(and `awk`). It runs anywhere with a shell: Linux, WSL, a Raspberry Pi, even a phone.

**Install** it on the far-end machine:

```sh
curl -fsSL https://raw.githubusercontent.com/junior/s3tx/v1.4.0/tx -o ~/.local/bin/tx
chmod +x ~/.local/bin/tx          # make sure ~/.local/bin is on your $PATH
tx version
```

<sub>To check the file, compare `sha256sum ~/.local/bin/tx` with the `tx` line of that
release's `SHA256SUMS`.</sub>

That one self-contained file is **all** the far end needs. Don't `git clone` the repo onto a
locked-down box: the rest (`s3tx`, `mediator/`, the IAM policy) is AWS-side tooling that has
no business there, and `tx` never depends on it.

**Configure once** in `~/.config/tx/config` (chmod 600: `TX_CLIENT_ID` is a secret). For
the mediator backend (matches `s3tx ... --to <client>`):

```sh
mkdir -p ~/.config/tx
cat > ~/.config/tx/config <<'EOF'
TX_MEDIATOR=https://your-mediator.vercel.app
TX_CLIENT_ID=your-machines-client-id      # this box's clientID; see mediator/
EOF
chmod 600 ~/.config/tx/config
```

Prefer the manifest backend (`s3tx ... --code`, no third party)? Run `s3tx manifest-url` on
the Mac: it prints the exact commands to set `TX_MANIFEST` or write
`~/.config/tx/manifest.url`.

**Profiles.** The file can also hold named profiles, each in a `[name]` section. The lines
before any section are the `[default]` profile, so a file without sections, like the one
above, keeps working as it is. `tx --profile k8s get <code>` uses the `[k8s]` section and
nothing else:

```ini
TX_MEDIATOR=https://your-mediator.vercel.app
TX_CLIENT_ID=your-machines-client-id

[k8s]
TX_MEDIATOR=https://tx.internal.example
TX_CLIENT_ID=this-machines-client-id-there
```

Environment variables override the default profile; a profile chosen with `--profile`
ignores them. The file is read, never run as shell code. `tx config` shows the settings in
use and where each one comes from, with secrets masked.

**Use it:**

```sh
tx get <code>              # resolve a code, then download
tx get <code> out.pdf      # ...to a chosen filename
tx get <code> /tmp/        # ...into a folder, under the file's own name
tx put ./photo.jpg <code>  # upload a file to a `drop` code (use - for stdin)
tx get "<https url>"       # a raw pre-signed URL works too, with no config at all
```

Anything starting with `http(s)://` is used directly; otherwise it's treated as a short
code and resolved via the mediator, or the manifest when the mediator does not know it.
`tx get` checks the target folder before it redeems a code, since a code works once. When
the file came from `s3tx send`, `tx get` also checks it against the SHA-256 s3tx recorded,
with `sha256sum`, `shasum` or `openssl`, whichever the box has; a file that does not match
is kept aside, never saved under its name. `tx version` says which release a box runs.

## Commands

| Command | Does |
|---------|------|
| `send FILE [KEY]` | upload a file (or stdin with `-`), print a download link |
| `link KEY` | fresh code/link for an object already in the bucket |
| `drop KEY` | print an upload link so the far end can push a file (no creds) |
| `recv KEY [OUT]` | download an object to this machine |
| `ls [PREFIX]` | list objects |
| `rm KEY` | delete one object |
| `rmrf` | delete every object (asks for confirmation) |
| `status` | show identity, bucket, and confirm the bucket is private |
| `lockdown` | force Block-Public-Access on, remove any bucket policy |
| `manifest-url` | print a long-lived seed URL for `--code` (max 7 days) |
| `codes` | list live manifest codes |
| `config` | show the settings in use and where each comes from (secrets masked) |
| `doctor` | check the setup end to end and say what to fix; exits 1 when a check fails |
| `--profile NAME` | before or after any command: use the `[NAME]` profile |
| `--version` | print the version and exit |

### Many files

`s3tx` reads and writes stdin, so pipe a tarball straight through it:

```sh
# you -> far end
tar czf - file1 dir/ | s3tx send - bundle.tgz --to laptop
#   far end:  tx get CODE          (then: tar xzf bundle.tgz)

# far end -> you
s3tx drop bundle.tgz --to laptop
#   far end:  tar czf - f1 dir/ | tx put - CODE
s3tx recv bundle.tgz               # then: tar xzf bundle.tgz
```

## Configuration

Settings come from environment variables or from `~/.config/s3tx/config` (chmod 600: it
can hold the admin token, and s3tx warns when others can read it). The file holds
`KEY=VALUE` lines named like the variables below, `#` comments, and optional named
profiles in `[name]` sections; the lines before any section are the `[default]` profile.
It is read, never executed, and `tx` uses the same format for its own file.

- **The default profile:** the environment overrides the file, setting by setting.
- **`s3tx --profile NAME ...`:** only the `[NAME]` section counts, never the environment
  and never `[default]`; whatever the section leaves out takes the built-in default. One
  profile's mediator or token can therefore never leak into another's.
- **`s3tx config`** shows the settings in effect and where each one comes from.

```ini
# the default profile: AWS, and a mediator on Vercel
S3TX_BUCKET=your-bucket
S3TX_MEDIATOR=https://your-mediator.vercel.app
S3TX_ADMIN_TOKEN=your-admin-token

[k8s]   # an S3-compatible server, and a mediator of its own
S3TX_AWS_PROFILE=internal-s3      # that server's keys, in ~/.aws/credentials
S3TX_BUCKET=transfers
S3TX_ENDPOINT_URL=https://s3.internal.example:9021
S3TX_CA_BUNDLE=/etc/ssl/certs/internal-ca.pem
S3TX_MEDIATOR=https://tx.internal.example
S3TX_ADMIN_TOKEN=that-mediators-admin-token
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `S3TX_AWS_PROFILE` | `s3tx` | AWS credentials profile; empty uses the standard boto3 chain (`S3TX_PROFILE` is its older name) |
| `S3TX_BUCKET` | `s3tx`, deprecated | bucket name; set it, since 2.0 drops the default (s3tx warns until then) |
| `S3TX_REGION` | `us-east-1` | bucket region; on AWS the bucket's own region is detected and used |
| `S3TX_ENDPOINT_URL` | (AWS) | an S3-compatible server to use instead of AWS |
| `S3TX_CA_BUNDLE` | (system) | CA bundle that verifies that server's certificate |
| `S3TX_EXPIRES` | `600` | link lifetime in seconds (clamped to the 7-day SigV4 max) |
| `S3TX_MANIFEST_KEY` | `.s3tx-manifest` | key of the private manifest object (for `--code`) |
| `S3TX_MEDIATOR` | (none) | mediator base URL (for `--to`) |
| `S3TX_ADMIN_TOKEN` | (none) | mediator admin bearer token (for `--to`) |

### S3-compatible servers

With `S3TX_ENDPOINT_URL` pointing at a server other than AWS, s3tx uses path-style
addresses and sends checksums only where the S3 API requires one, which suits
S3-compatible servers. It skips what only AWS has: the identity check before each command
and the Block Public Access check in `status`, and `lockdown` refuses. Keep such a bucket
private with the server's own controls. A mediator accepts only `https` URLs on AWS by
default; for another server, set the mediator's `ALLOWED_URL_HOST` to its host name.

## Security model

- A pre-signed URL is a **bearer token**: whoever holds it has the access it encodes until
  it expires. `s3tx` prints the expiry and treats URLs as secrets: keep them out of shared
  logs and chat history.
- The bucket stays private; on AWS, `s3tx status` checks Block-Public-Access and
  `s3tx lockdown` enforces it.
- Credentials never leave the trusted machine, and the IAM user is scoped to one bucket.
- **Mediator codes** (`--to`) are single-use, scoped to one client, and rate-limited.
- **Manifest codes** (`--code`) are simpler but weaker: the *seed URL* from
  `s3tx manifest-url` (valid up to 7 days) grants read of the whole manifest. Prefer `--to`
  when you have the mediator, and treat the seed URL itself as a secret.
- Every mediator answer is marked `Cache-Control: no-store`, so no proxy on the way keeps a
  URL, and a file sent with `s3tx send` is checked end to end by its SHA-256.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## The mediator

`--to` needs the small Vercel app in [`mediator/`](mediator/): it maps a short code to a
pre-signed URL with single-use, per-client scoping, and rate-limiting. It only ever holds
short-lived URLs and never your AWS credentials. Setup and the threat model are in
[`mediator/README.md`](mediator/README.md).

## Development

```sh
uvx ruff check .                                                   # lint
uvx ruff format .                                                  # format
uv run --with-requirements tests/requirements.txt python -m pytest tests/
(cd mediator && npm ci --ignore-scripts && npm audit && npm test)  # mediator handlers
```

No test needs AWS, a deployed mediator or Redis: `s3tx` runs against moto, `tx` runs
under `sh` and `dash` against a local stand-in for the mediator, full round trips use the
real scripts and `curl`, and the mediator handlers run against an in-memory Redis. Known
gaps are tests marked `xfail(strict=True)` or `todo` that describe the intended behaviour.
The command line is pinned in `tests/golden/cli-contract.json`; after a deliberate change,
regenerate it with `UPDATE_GOLDEN=1` and review its diff.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs all of this on Python
3.11 and the newest Python, and on 3.9, which is unsupported but has to keep working; the
mediator on the Node.js its `package.json` pins, after `npm audit`; and `shellcheck` on
`tx`. It also runs by hand on any branch (`gh workflow run ci.yml --ref BRANCH`).

### Releasing

1. Set `__version__` in `s3tx` and `version` in `tx`, date the Unreleased section of
   [`CHANGELOG.md`](CHANGELOG.md), and move the install lines above to the new tag. The
   tests fail until they agree.
2. Merge, then tag the release commit, signed (`git tag -s vX.Y.Z <commit>`), and push the
   tag. The [release workflow](.github/workflows/release.yml) checks that the tag names the
   same version, publishes the GitHub Release with the CHANGELOG section as its notes and
   `s3tx`, `tx` and `SHA256SUMS` attached and attested, and then points the Homebrew
   formula in `junior/homebrew-tap` at the tag. A tag with a hyphen is a pre-release and
   leaves the formula alone.

## License

[MIT](LICENSE).
