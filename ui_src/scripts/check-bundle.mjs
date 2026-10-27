// Bundle gate (zero-drift amendment 3, ui-build row): the gzip size of
// what the initial route loads stays under the 350 kB per-route budget
// set from the Task 3 probe data (ui-spa-program-plan.md: "per initial
// route"). Wired into `pnpm build` and CI (poe ui-build, since c7856ff).
//
// Two checks:
//   1. EVERY js/css asset stays under the per-chunk cap (lazy stages
//      included — no chunk may grow past the budget on its own).
//   2. The INITIAL ROUTE total stays under the budget. The route set is
//      what a first visit downloads: the entry chunk named in
//      index.html with its static-import closure, and the LANDING stage
//      chunk (Setup — App.tsx redirects / there) with its own static
//      closure, so the
//      measure matches the plan's probe arithmetic (shell + landing
//      screen + shared deps). Other stage chunks load on navigation,
//      not on landing; each stays capped by check 1.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LIMIT_KB = 350;
// The landing stage assumption: App.tsx redirects / to /setup. A Home-*
// chunk means that changed — fail loudly instead of measuring the wrong
// route.
const LANDING_STAGE_PREFIX = "Setup-";

const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const distDir = join(uiRoot, "..", "src", "corpus_kb", "ui_dist");
const distAssets = join(distDir, "assets");

let names;
try {
  names = readdirSync(distAssets);
} catch {
  console.error(`check-bundle: no build output under ${distAssets}. Run the build first.`);
  process.exit(1);
}
const assets = names.filter((n) => n.endsWith(".js") || n.endsWith(".css")).sort();
if (assets.length === 0) {
  console.error(`check-bundle: no js/css assets under ${distAssets}. Run the build first.`);
  process.exit(1);
}

const kbOf = (name) => gzipSync(readFileSync(join(distAssets, name))).length / 1024;

// The entry chunk and the route stylesheet come from index.html.
let entry = null;
let routeCss = null;
try {
  const html = readFileSync(join(distDir, "index.html"), "utf-8");
  entry = /assets\/([\w.-]+\.js)/.exec(html)?.[1] ?? null;
  routeCss = /assets\/([\w.-]+\.css)/.exec(html)?.[1] ?? null;
} catch {
  console.error("check-bundle: cannot read index.html next to the assets.");
  process.exit(1);
}
if (entry === null || !assets.includes(entry)) {
  console.error(`check-bundle: entry chunk from index.html not found among the assets.`);
  process.exit(1);
}
if (routeCss !== null && !assets.includes(routeCss)) {
  console.error(`check-bundle: stylesheet from index.html not found among the assets.`);
  process.exit(1);
}

// Static-import closure of the entry chunk. Only `from"x.js"` and bare
// `import"x.js"` forms match; a dynamic `import("x.js")` carries a
// parenthesis before the quote and stays outside the route set.
function staticImports(name) {
  const src = readFileSync(join(distAssets, name), "utf-8");
  const found = new Set();
  const re = /(?:from|import)\s*"\.\/([\w.-]+\.js)"/g;
  for (const match of src.matchAll(re)) {
    if (assets.includes(match[1])) {
      found.add(match[1]);
    }
  }
  return found;
}

const routeSet = new Set([entry, ...(routeCss !== null ? [routeCss] : [])]);
// The landing lookup scans chunks only: a future Setup-*.css would
// otherwise shadow the real Setup chunk here.
const landing = assets
  .filter((name) => name.endsWith(".js"))
  .find((name) => name.startsWith(LANDING_STAGE_PREFIX));
if (landing === undefined) {
  if (assets.some((name) => name.startsWith("Home-"))) {
    console.error(
      "check-bundle: a Home stage chunk exists, so the landing route is no longer " +
        "Setup. Update LANDING_STAGE_PREFIX before trusting this gate.",
    );
  } else {
    console.error(
      `check-bundle: landing stage chunk (${LANDING_STAGE_PREFIX}*) not found; ` +
        "the route total would not match a first visit.",
    );
  }
  process.exit(1);
}
routeSet.add(landing);
const queue = [entry, landing];
while (queue.length > 0) {
  for (const dep of staticImports(queue.shift())) {
    if (!routeSet.has(dep)) {
      routeSet.add(dep);
      queue.push(dep);
    }
  }
}

let failed = false;
for (const name of assets) {
  const kb = kbOf(name);
  const over = kb > LIMIT_KB;
  failed ||= over;
  const mark = routeSet.has(name) ? "route" : "lazy ";
  console.log(
    `${over ? "FAIL" : "ok  "} ${kb.toFixed(2).padStart(8)} kB gzip  [${mark}]  ${name}` +
      (over ? `  (over the ${LIMIT_KB} kB per-chunk limit)` : ""),
  );
}

const routeKB = [...routeSet].reduce((sum, name) => sum + kbOf(name), 0);
const routeOver = routeKB > LIMIT_KB;
failed ||= routeOver;
console.log(
  `${routeOver ? "FAIL" : "ok  "} ${routeKB.toFixed(2).padStart(8)} kB gzip  TOTAL (initial route: ` +
    `shell + landing stage + shared) / ${LIMIT_KB} kB budget`,
);
if (failed) {
  console.error("check-bundle: bundle over budget. Drop the heaviest usage or split chunks.");
  process.exit(1);
}
