// Copies the committed Python-side op manifest into src/api/ so both tsc
// and vite see it as an importable JSON module (package data stays the
// single committed copy; this file is regenerated before lint/test/build).
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const uiSrc = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(uiSrc, "..", "src", "corpus_kb", "ui_manifest.json");
const target = join(uiSrc, "src", "api", "manifest.json");
mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log("copied ui_manifest.json -> src/api/manifest.json");
