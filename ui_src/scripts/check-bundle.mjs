// Bundle gate (zero-drift amendment 3, ui-build row): the gzip size of
// what the initial route loads stays under the 350 kB per-route budget
// set from the Task 3 probe data (ui-spa-program-plan.md). Wired into
// `pnpm build`; the poe target and CI wiring land with the integrator
// (map rule 10: a gate needs poe + CI + map row together).
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LIMIT_KB = 350;

const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const distAssets = join(uiRoot, "..", "src", "corpus_kb", "ui_dist", "assets");

let names;
try {
  names = readdirSync(distAssets);
} catch {
  console.error(`check-bundle: no build output under ${distAssets}. Run the build first.`);
  process.exit(1);
}

const rows = [];
let totalKB = 0;
for (const name of names.filter((n) => n.endsWith(".js") || n.endsWith(".css")).sort()) {
  const kb = gzipSync(readFileSync(join(distAssets, name))).length / 1024;
  rows.push({ name, kb });
  totalKB += kb;
}

if (rows.length === 0) {
  console.error(`check-bundle: no js/css assets under ${distAssets}. Run the build first.`);
  process.exit(1);
}

let failed = false;
for (const { name, kb } of rows) {
  const over = kb > LIMIT_KB;
  failed ||= over;
  console.log(
    `${over ? "FAIL" : "ok  "} ${kb.toFixed(2).padStart(8)} kB gzip  ${name}` +
      (over ? `  (over the ${LIMIT_KB} kB per-chunk limit)` : ""),
  );
}
const totalOver = totalKB > LIMIT_KB;
failed ||= totalOver;
console.log(
  `${totalOver ? "FAIL" : "ok  "} ${totalKB.toFixed(2).padStart(8)} kB gzip  TOTAL (initial route)` +
    ` / ${LIMIT_KB} kB budget`,
);
if (failed) {
  console.error("check-bundle: bundle over budget. Drop the heaviest usage or split chunks.");
  process.exit(1);
}
