# mediator

A tiny Vercel app that lets `tx` (on a laptop, a Pi, …) redeem a **short, scoped,
single-use code** for a pre-signed S3 URL — so nobody hand-types a 500-char URL.

It is the `TX_MEDIATOR` backend for `tx` and the `--to` target for `s3tx` (see the
[main README](../README.md)). The bucket stays private; AWS credentials stay on the
Mac. The mediator only ever holds short-lived pre-signed URLs.

## Protocol

```
register (Mac):  s3tx → POST /api/register   Authorization: Bearer <ADMIN_TOKEN>
                 { code, url, client:"laptop", method, ttl }     → stored, single-use, TTL
redeem  (far):   tx   → GET  /api/get/<code>  X-Client-Id: <clientID>
                 → 200 text/plain <pre-signed URL>     (then tx GETs/PUTs it)
```

Security properties:
- **Client auth**: the far end proves identity with a per-machine secret (`clientID`),
  sent as a header, never in a URL. The mediator stores only `sha256(clientID)`.
- **Scoped**: each code names the one client allowed to redeem it. A leaked code is
  useless to any other machine.
- **Single-use + TTL**: the code is deleted on first successful redeem and expires
  (default 10 min) regardless.
- **Lockout**: after `MAX_ATTEMPTS` (default 20) failures per client in a 10-min
  window → HTTP 429, so the 8-char code space can't be brute-forced.
- **URL allow-list**: register rejects any URL that isn't an `https://…amazonaws.com`
  host. Set `ALLOWED_URL_HOST` to pin the exact bucket host.

## One-time setup

> Deploying from this monorepo: set the Vercel project's **Root Directory** to
> `mediator/` so only this folder is built.

1. **Provision Redis (Upstash via the Vercel Marketplace).** Upstash Redis gives TTL +
   atomic single-use. From this directory:
   ```sh
   vercel link            # link it to your team, create the project
   vercel install upstash # or add it from the dashboard → Storage → Marketplace
   ```
   This injects `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` (the code also
   accepts `KV_REST_API_URL` / `KV_REST_API_TOKEN`).

2. **Set the app secrets:**
   ```sh
   vercel env add ADMIN_TOKEN production       # paste: openssl rand -hex 32
   vercel env add CLIENTS production           # paste the JSON from step 3
   vercel env add ALLOWED_URL_HOST production  # optional: pin the exact bucket host
   ```

3. **Register each client machine.** For every far-end box generate a secret and hash it:
   ```sh
   CLIENTID=$(openssl rand -hex 16)                              # → goes in tx's config
   HASH=$(printf %s "$CLIENTID" | shasum -a 256 | cut -d' ' -f1) # → goes in CLIENTS
   echo "$CLIENTID  ->  $HASH"
   ```
   `CLIENTS` is a JSON map of `hash → name`, e.g.:
   ```json
   {"<hash-a>":"laptop","<hash-b>":"rpi"}
   ```
   The `clientID` is a secret — store it in the far end's tx config (chmod 600), never
   in a repo. Adding a machine later = append to `CLIENTS` and redeploy.

4. **Deploy:** `vercel deploy --prod`. Note the `*.vercel.app` URL.

## Wiring the tools

- **Mac (`s3tx`)** — set locally (untracked):
  `S3TX_MEDIATOR=https://your-mediator.vercel.app`, `S3TX_ADMIN_TOKEN=…`
  Then `s3tx send report.pdf --to laptop` prints a code.
- **Far end (`tx`)** — in its config: `TX_MEDIATOR=https://your-mediator.vercel.app`,
  `TX_CLIENT_ID=<that machine's clientID>`. Then `tx get <code>`.

## Endpoints
- `POST /api/register` — admin-only; stores `{url, client, method}` at `code:<code>` with TTL.
- `GET  /api/get/<code>` — client-authed; returns the URL as text/plain, single-use.
