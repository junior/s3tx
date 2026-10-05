// POST /api/register, called by s3tx: the admin token, input checks, ttl clamping and
// the refusal to overwrite a live code.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { call, sha256 } from "./harness.mjs";

process.env.ADMIN_TOKEN = "admin-secret";
process.env.CLIENTS = JSON.stringify({ [sha256("laptop-secret")]: "laptop" });
process.env.CLIENTS_EXTRA = JSON.stringify({ [sha256("pi-secret")]: "pi" });
delete process.env.ALLOWED_URL_HOST;

const { default: register } = await import("../api/register.js");
const { fake } = await import("@upstash/redis");

const URL_OK = "https://example-bucket.s3.amazonaws.com/report.pdf?X-Amz-Signature=abc";
const AUTH = { authorization: "Bearer admin-secret" };
const post = (body, headers = AUTH) => call(register, { method: "POST", headers, body });
const good = (extra = {}) => ({ code: "Abcd2345", url: URL_OK, client: "laptop", ...extra });
const stored = (code) => JSON.parse(fake.entry(`code:${code}`).value);

beforeEach(() => {
  fake.reset();
  delete process.env.ALLOWED_URL_HOST;
});

test("only POST is accepted", async () => {
  assert.equal((await call(register, { method: "GET", headers: AUTH })).statusCode, 405);
});

test("a missing or wrong admin token is refused", async () => {
  for (const headers of [{}, { authorization: "Bearer nope" }, { authorization: "admin-secret!" }]) {
    const res = await post(good(), headers);
    assert.equal(res.statusCode, 401);
    assert.deepEqual(res.body, { error: "unauthorized" });
  }
});

test("the Bearer prefix is case-insensitive", async () => {
  assert.equal((await post(good(), { authorization: "bearer admin-secret" })).statusCode, 200);
});

test("with no ADMIN_TOKEN configured nothing is accepted", async () => {
  const saved = process.env.ADMIN_TOKEN;
  delete process.env.ADMIN_TOKEN;
  try {
    assert.equal((await post(good(), { authorization: "Bearer " })).statusCode, 401);
    assert.equal((await post(good(), {})).statusCode, 401);
  } finally {
    process.env.ADMIN_TOKEN = saved;
  }
});

test("a code is 6 to 32 letters and digits", async () => {
  for (const code of ["abc12", "a".repeat(33), "abc-1234", "", undefined]) {
    const res = await post(good({ code }));
    assert.equal(res.statusCode, 400, String(code));
    assert.deepEqual(res.body, { error: "bad code" });
  }
  assert.equal((await post(good({ code: "abc123" }))).statusCode, 200);
});

test("the URL must be https on an AWS host", async () => {
  const bad = [
    "http://example-bucket.s3.amazonaws.com/x",
    "https://evil.example/x",
    "https://amazonaws.com.evil.example/x",
    "https://evilamazonaws.com/x",
    "not a url",
    undefined,
  ];
  for (const url of bad) {
    const res = await post(good({ url }));
    assert.equal(res.statusCode, 400, String(url));
    assert.deepEqual(res.body, { error: "bad url" });
  }
});

test("ALLOWED_URL_HOST pins the exact bucket host", async () => {
  process.env.ALLOWED_URL_HOST = "example-bucket.s3.amazonaws.com";
  assert.equal((await post(good())).statusCode, 200);
  const other = await post(good({ code: "Other234", url: "https://other.s3.amazonaws.com/x" }));
  assert.equal(other.statusCode, 400);
});

test("the target client must be known, from CLIENTS or CLIENTS_EXTRA", async () => {
  const res = await post(good({ client: "stranger" }));
  assert.equal(res.statusCode, 400);
  assert.deepEqual(res.body, { error: "unknown target client" });
  assert.equal((await post(good({ client: "pi" }))).statusCode, 200);
});

test("the method is GET or PUT, GET when omitted", async () => {
  assert.deepEqual((await post(good({ method: "DELETE" }))).body, { error: "bad method" });
  assert.equal((await post(good({ code: "Put23456", method: "PUT" }))).statusCode, 200);
  assert.equal(stored("Put23456").method, "PUT");
  assert.equal((await post(good())).statusCode, 200);
  assert.deepEqual(stored("Abcd2345"), { url: URL_OK, client: "laptop", method: "GET" });
});

test("ttl is clamped to 30..900 seconds, and 600 when missing, zero or invalid", async () => {
  const now = 1_000_000;
  fake.setClock(() => now);
  const cases = [[5, 30], [5000, 900], [120, 120], ["300", 300], ["abc", 600], [0, 600], [undefined, 600]];
  for (const [i, [ttl, expected]] of cases.entries()) {
    const code = `Ttl${i}2345`;
    const res = await post(good({ code, ttl }));
    assert.equal(res.statusCode, 200, String(ttl));
    assert.deepEqual(res.body, { ok: true, ttl: expected });
    assert.equal(fake.entry(`code:${code}`).expiresAt, now + expected * 1000);
  }
});

test("a live code is never overwritten; s3tx picks another", async () => {
  assert.equal((await post(good())).statusCode, 200);
  const res = await post(good({ url: "https://example-bucket.s3.amazonaws.com/other" }));
  assert.equal(res.statusCode, 409);
  assert.deepEqual(res.body, { error: "code exists" });
  assert.equal(stored("Abcd2345").url, URL_OK);
});

test("an expired code can be registered again", async () => {
  let now = 1_000_000;
  fake.setClock(() => now);
  assert.equal((await post(good({ ttl: 30 }))).statusCode, 200);
  now += 31_000;
  assert.equal((await post(good())).statusCode, 200);
});
