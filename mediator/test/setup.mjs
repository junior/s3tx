// Loaded with `node --import ./test/setup.mjs`: installs the resolve hook before any
// handler is imported.
import { register } from "node:module";

register("./hooks.mjs", import.meta.url);
