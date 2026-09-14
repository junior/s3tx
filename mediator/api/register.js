// POST /api/register  — called by s3tx (the Mac) to stash a pre-signed URL behind
// a short, scoped, single-use code. Guarded by the ADMIN_TOKEN bearer secret.
//
// Body (JSON): { code, url, client, method?, ttl? }
//   code   8+ alphanumerics (the thing the human carries)
//   url    the pre-signed S3 URL (GET or PUT)
//   client the TARGET client name this code may be redeemed by (must be allow-listed)
//   method "GET" | "PUT" (informational; the URL itself is method-specific)
//   ttl    seconds (clamped 30..900)
import { Redis } from "@upstash/redis";
import { timingSafeEqual } from "node:crypto";

const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL,
  token: process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN,
});

// CLIENTS is a write-only ("sensitive") Vercel variable, so it cannot be read back to
// append to it. CLIENTS_EXTRA is a second, readable map merged on top: register new
// machines there and leave CLIENTS untouched.
function parseMap(raw) {
  try {
    const v = JSON.parse(raw || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}
function clientNames() {
  return Object.values({ ...parseMap(process.env.CLIENTS), ...parseMap(process.env.CLIENTS_EXTRA) });
}

function safeEqual(a, b) {
  const ab = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}

// The pre-signed URL must point at AWS S3 (or an operator-pinned host), so a leaked
// ADMIN_TOKEN can't turn the mediator into a redirect to an arbitrary host. Set
// ALLOWED_URL_HOST to pin the exact bucket host (e.g. my-bucket.s3.us-east-1.amazonaws.com).
function urlAllowed(raw) {
  let u;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (u.protocol !== "https:") return false;
  const pinned = process.env.ALLOWED_URL_HOST;
  if (pinned) return u.hostname === pinned;
  return /(^|\.)amazonaws\.com$/.test(u.hostname);
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "method not allowed" });

  const auth = (req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (!process.env.ADMIN_TOKEN || !safeEqual(auth, process.env.ADMIN_TOKEN)) {
    return res.status(401).json({ error: "unauthorized" });
  }

  const { code, url, client, method = "GET", ttl = 600 } = req.body || {};
  if (!/^[A-Za-z0-9]{6,32}$/.test(code || "")) return res.status(400).json({ error: "bad code" });
  if (!urlAllowed(url || "")) return res.status(400).json({ error: "bad url" });
  if (!clientNames().includes(client)) return res.status(400).json({ error: "unknown target client" });
  if (!["GET", "PUT"].includes(method)) return res.status(400).json({ error: "bad method" });
  const ttlSec = Math.min(Math.max(parseInt(ttl, 10) || 600, 30), 900);

  // nx: refuse to clobber an existing live code (collision → s3tx regenerates).
  const ok = await redis.set(`code:${code}`, JSON.stringify({ url, client, method }), {
    nx: true,
    ex: ttlSec,
  });
  if (ok === null) return res.status(409).json({ error: "code exists" });
  return res.status(200).json({ ok: true, ttl: ttlSec });
}
