// Shared helpers: call a handler the way Vercel's Node runtime does, with a minimal
// request and a response object that records what the handler sent.
import { createHash } from "node:crypto";

export const sha256 = (s) => createHash("sha256").update(s).digest("hex");

export function call(handler, { method = "GET", headers = {}, body, query = {} } = {}) {
  return new Promise((resolve, reject) => {
    const res = {
      statusCode: 200,
      headers: {},
      body: undefined,
      status(code) {
        this.statusCode = code;
        return this;
      },
      setHeader(name, value) {
        this.headers[name.toLowerCase()] = value;
        return this;
      },
      json(payload) {
        this.body = payload;
        resolve(this);
        return this;
      },
      send(payload) {
        this.body = payload;
        resolve(this);
        return this;
      },
    };
    Promise.resolve(handler({ method, headers, body, query }, res)).catch(reject);
  });
}
