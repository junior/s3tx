// In-memory stand-in for @upstash/redis, swapped in for the real package by hooks.mjs.
// It implements only what the handlers call, the way Upstash's client behaves by
// default: values are stored as strings and get() hands JSON back parsed. Every call
// yields to the event loop first, as a network round trip would.
const store = new Map(); // key -> { value: string, expiresAt: number | null }
let clock = () => Date.now();

const tick = () => new Promise((resolve) => setImmediate(resolve));

function live(key) {
  const entry = store.get(key);
  if (!entry) return undefined;
  if (entry.expiresAt !== null && entry.expiresAt <= clock()) {
    store.delete(key);
    return undefined;
  }
  return entry;
}

function add(key, delta) {
  const entry = live(key);
  const n = (entry ? Number(entry.value) : 0) + delta;
  store.set(key, { value: String(n), expiresAt: entry ? entry.expiresAt : null });
  return n;
}

export class Redis {
  constructor(_options) {}

  async set(key, value, { nx = false, ex } = {}) {
    await tick();
    if (nx && live(key)) return null;
    const stored = typeof value === "string" ? value : JSON.stringify(value);
    store.set(key, { value: stored, expiresAt: ex ? clock() + ex * 1000 : null });
    return "OK";
  }

  async get(key) {
    await tick();
    const entry = live(key);
    if (!entry) return null;
    try {
      return JSON.parse(entry.value);
    } catch {
      return entry.value;
    }
  }

  async incr(key) {
    await tick();
    return add(key, 1);
  }

  async decr(key) {
    await tick();
    return add(key, -1);
  }

  async del(key) {
    await tick();
    return store.delete(key) ? 1 : 0;
  }
}

// Test controls: clear the store, move the clock, read what a key holds.
export const fake = {
  reset() {
    store.clear();
    clock = () => Date.now();
  },
  setClock(fn) {
    clock = fn;
  },
  entry(key) {
    return live(key);
  },
};
