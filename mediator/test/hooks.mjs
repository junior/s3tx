// Module resolve hook for the tests: "@upstash/redis" becomes the in-memory fake, so
// the handlers run unmodified with no network and no installed packages.
const FAKE = new URL("./fake-redis.mjs", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@upstash/redis") return { url: FAKE, shortCircuit: true };
  return nextResolve(specifier, context);
}
