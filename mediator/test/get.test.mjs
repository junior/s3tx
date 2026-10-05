// GET /api/get/<code>, called by tx: which client is asking, the per-client rate limit,
// the scope check and single use.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { call, sha256 } from "./harness.mjs";

const CLIENTS = JSON.stringify({
  [sha256("laptop-secret")]: "laptop",
  [sha256("shared-secret")]: "old-name",
});
const CLIENTS_EXTRA = JSON.stringify({
  [sha256("pi-secret")]: "pi",
  [sha256("shared-secret")]: "new-name",
});
process.env.CLIENTS = CLIENTS;
process.env.CLIENTS_EXTRA = CLIENTS_EXTRA;
delete process.env.MAX_ATTEMPTS; // the default, 20

const { default: redeem } = await import("../api/get/[code].js");
const { Redis, fake } = await import("@upstash/redis");
const redis = new Redis();

const URL_OK = "https://example-bucket.s3.amazonaws.com/r.pdf?X-Amz-Signature=abc";
const get = (code, clientId) =>
  call(redeem, {
    method: "GET",
    headers: clientId === undefined ? {} : { "x-client-id": clientId },
    query: { code },
  });
const seed = (code, client = "laptop", ttl = 600) =>
  redis.set(`code:${code}`, JSON.stringify({ url: URL_OK, client, method: "GET" }), { nx: true, ex: ttl });

beforeEach(() => {
  fake.reset();
  process.env.CLIENTS = CLIENTS;
  process.env.CLIENTS_EXTRA = CLIENTS_EXTRA;
});

test("a request without X-Client-Id is refused", async () => {
  const res = await get("Abcd2345");
  assert.equal(res.statusCode, 401);
  assert.equal(res.body, "missing X-Client-Id");
});

test("an unknown client is refused", async () => {
  const res = await get("Abcd2345", "stranger-secret");
  assert.equal(res.statusCode, 403);
  assert.equal(res.body, "unknown client");
});

test("the owner redeems a code for its URL, as plain text", async () => {
  await seed("Abcd2345");
  const res = await get("Abcd2345", "laptop-secret");
  assert.equal(res.statusCode, 200);
  assert.equal(res.body, URL_OK);
  assert.equal(res.headers["content-type"], "text/plain; charset=utf-8");
});

test("a code works once", async () => {
  await seed("Abcd2345");
  assert.equal((await get("Abcd2345", "laptop-secret")).statusCode, 200);
  const again = await get("Abcd2345", "laptop-secret");
  assert.equal(again.statusCode, 404);
  assert.equal(again.body, "not found or expired");
});

test("another client cannot redeem it, and does not burn it", async () => {
  await seed("Abcd2345", "laptop");
  const res = await get("Abcd2345", "pi-secret");
  assert.equal(res.statusCode, 403);
  assert.equal(res.body, "not your code");
  assert.equal((await get("Abcd2345", "laptop-secret")).statusCode, 200);
});

test("an expired code is gone", async () => {
  let now = 1_000_000;
  fake.setClock(() => now);
  await seed("Abcd2345", "laptop", 30);
  now += 31_000;
  assert.equal((await get("Abcd2345", "laptop-secret")).statusCode, 404);
});

test("a malformed code is refused", async () => {
  for (const code of ["ab12", "bad code", "a".repeat(33), undefined]) {
    const res = await get(code, "laptop-secret");
    assert.equal(res.statusCode, 400, String(code));
    assert.equal(res.body, "bad code");
  }
});

test("CLIENTS_EXTRA adds clients and wins over CLIENTS for the same secret", async () => {
  await seed("Pi234567", "pi");
  assert.equal((await get("Pi234567", "pi-secret")).statusCode, 200);
  await seed("New23456", "new-name");
  assert.equal((await get("New23456", "shared-secret")).statusCode, 200);
  await seed("Old23456", "old-name");
  assert.equal((await get("Old23456", "shared-secret")).statusCode, 403);
});

test("a malformed CLIENTS_EXTRA is ignored and CLIENTS keeps working", async () => {
  process.env.CLIENTS_EXTRA = "{not json";
  await seed("Abcd2345");
  assert.equal((await get("Abcd2345", "laptop-secret")).statusCode, 200);
  assert.equal((await get("Abcd2345", "pi-secret")).statusCode, 403);
});

test("a malformed CLIENTS is ignored and CLIENTS_EXTRA keeps working", async () => {
  process.env.CLIENTS = "[1, 2]"; // an array is not a map
  await seed("Pi234567", "pi");
  assert.equal((await get("Pi234567", "pi-secret")).statusCode, 200);
  assert.equal((await get("Abcd2345", "laptop-secret")).statusCode, 403);
});

test("20 attempts per client in the window, then 429; other clients unaffected", async () => {
  for (let i = 0; i < 20; i++) {
    assert.equal((await get(`Miss${String(i).padStart(4, "0")}`, "laptop-secret")).statusCode, 404);
  }
  const res = await get("Miss9999", "laptop-secret");
  assert.equal(res.statusCode, 429);
  assert.equal(res.body, "rate limited, try later");
  assert.equal((await get("Miss9999", "pi-secret")).statusCode, 404);
});

test("the lockout ends with its 10-minute window", async () => {
  let now = 1_000_000;
  fake.setClock(() => now);
  for (let i = 0; i < 21; i++) await get("Miss2345", "laptop-secret");
  assert.equal((await get("Miss2345", "laptop-secret")).statusCode, 429);
  now += 601_000;
  assert.equal((await get("Miss2345", "laptop-secret")).statusCode, 404);
});

test("successful redemptions do not use up the budget", async () => {
  for (let i = 0; i < 30; i++) {
    const code = `Ok${String(i).padStart(6, "0")}`;
    await seed(code);
    assert.equal((await get(code, "laptop-secret")).statusCode, 200, code);
  }
});

test(
  "two simultaneous redemptions of one code: exactly one wins",
  { todo: "get then del is not atomic, so both can win; use GETDEL" },
  async () => {
    await seed("Abcd2345");
    const both = await Promise.all([get("Abcd2345", "laptop-secret"), get("Abcd2345", "laptop-secret")]);
    assert.deepEqual(both.map((r) => r.statusCode).sort(), [200, 404]);
  },
);
