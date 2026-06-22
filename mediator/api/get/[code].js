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

function clients() {
  try {
    return JSON.parse(process.env.CLIENTS || "{}");
  } catch {
    return {};
  }
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
