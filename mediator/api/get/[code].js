// GET /api/get/<code>  — called by tx (the far end) to redeem a code for its URL.
// Auth: header "X-Client-Id: <clientID>" (the per-machine secret; never in the URL).
//
// Checks, in order: known client (hash in allow-list) → per-client rate-limit →
// code exists/unexpired → code is scoped to THIS client → single-use (deleted on
// success). Returns the pre-signed URL as text/plain so tx needs no JSON parser.
import { Redis } from "@upstash/redis";
import { createHash } from "node:crypto";

const redis = new Redis({
  url: process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL,
  token: process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN,
});
const MAX_ATTEMPTS = parseInt(process.env.MAX_ATTEMPTS || "20", 10);
const WINDOW = 600; // seconds; lockout window per client

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
function clients() {
  return { ...parseMap(process.env.CLIENTS), ...parseMap(process.env.CLIENTS_EXTRA) };
}

function text(res, status, body) {
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  return res.status(status).send(body);
}

export default async function handler(req, res) {
  const clientId = req.headers["x-client-id"];
  if (!clientId) return text(res, 401, "missing X-Client-Id");

  // Identify the client by the SHA-256 of its secret (we never store the secret).
  const hash = createHash("sha256").update(String(clientId)).digest("hex");
  const name = clients()[hash];
  if (!name) return text(res, 403, "unknown client");

  // Per-client lockout: count attempts in a rolling window; successes are refunded.
  // Create the counter together with its TTL (NX) so an interrupted request can never
  // leave a counter without an expiry — which would lock the client out permanently.
  const rlKey = `rl:${name}`;
  await redis.set(rlKey, 0, { nx: true, ex: WINDOW });
  const attempts = await redis.incr(rlKey);
  if (attempts > MAX_ATTEMPTS) return text(res, 429, "rate limited, try later");

  const code = req.query.code;
  if (!/^[A-Za-z0-9]{6,32}$/.test(code || "")) return text(res, 400, "bad code");

  const raw = await redis.get(`code:${code}`);
  if (!raw) return text(res, 404, "not found or expired");
  const entry = typeof raw === "string" ? JSON.parse(raw) : raw;

  if (entry.client !== name) return text(res, 403, "not your code"); // scope check (no delete)

  await redis.del(`code:${code}`); // single-use: burn it now that the client is verified
  await redis.decr(rlKey); // a legitimate success shouldn't erode the rate budget
  return text(res, 200, entry.url);
}
