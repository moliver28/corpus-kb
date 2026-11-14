// Import-graph gate (Phase-2 Wave 6, zero-drift rule 10; Wave 7 extends
// it to the chat packages): the heavy libraries stay out of the initial
// route. Two checks per library family:
//   1. SOURCE: the packages are imported ONLY in the family's lazy
//      module set (map: pages/Map.tsx + pages/map/; chat: pages/Chat.tsx
//      + pages/chat/).
//   2. BUILT OUTPUT: the entry route set (the same closure walk the
//      bundle gate uses) holds no chunk carrying the family's runtime
//      marker — the libraries physically never reach a first visit.
// Wired as `poe ui-import-graph` and a ci.yml / ui step. The source
// scan and the closure walk are exported for
// check-import-graph.test.mjs (vitest, rides `poe ui-test`): its
// fixture trees prove the gate FAILS on a leak — static, dagre, and
// dynamic — and PASSES a clean tree.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// The ONLY modules allowed to import the map libraries (the lazy Map
// chunk's closure). Everything else fails the source scan. The vitest
// pins this list exactly: widening it would let a leak pass silently.
export const ALLOWED_PREFIXES = ["pages/Map.tsx", "pages/map/"];
export const LIB_MARKER = "react-flow__node";
export const LIB_PACKAGES = ["@xyflow/react", "@dagrejs/dagre"];

// Wave 7: the @ant-design/x family rides the lazy Chat chunk ONLY.
// The chat rules mirror the map family: the prefixes are the lazy Chat
// module set, the marker is X's cssinjs style-layer name observed in a
// real build — it lands in exactly one chunk, the lazy Chat chunk
// (re-pin from the build if upstream renames it) — and the
// zero-import guard is ON now that the import exists. The vitest pins
// the prefix and package lists; the marker + guard pin is the dep
// commit itself.
export const CHAT_ALLOWED_PREFIXES = ["pages/Chat.tsx", "pages/chat/"];
export const CHAT_PACKAGES = ["@ant-design/x", "@ant-design/x-markdown", "@ant-design/x-sdk"];
export const CHAT_MARKER = "antdx";
export const CHAT_REQUIRE_IMPORTS = true;

const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const srcDir = join(uiRoot, "src");
const distDir = join(uiRoot, "..", "src", "corpus_kb", "ui_dist");
const distAssets = join(distDir, "assets");

/** Every .ts/.tsx file under dir, as a flat generator. */
export function* collectSourceFiles(dir) {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, name.name);
    if (name.isDirectory()) {
      yield* collectSourceFiles(path);
    } else if (/\.(ts|tsx)$/.test(name.name)) {
      yield path;
    }
  }
}

/**
 * The source scan: the map libraries are imported ONLY inside the
 * allowed prefixes. Answers the offender paths (relative, forward
 * slashes) and how many files import the libraries at all — 0 means
 * the scan is lying (the package list drifted), not a clean tree.
 */
export function scanSource(dir, config = {}) {
  const allowed = config.allowedPrefixes ?? ALLOWED_PREFIXES;
  const libs = config.libPackages ?? LIB_PACKAGES;
  // One statement form: static (`from "..."`, bare `import "..."`) or
  // dynamic (`import("...")`). The `[(]?` arm is what catches the
  // lazy `import("@xyflow/react")` form.
  const pattern = new RegExp(`(?:from|import)\\s*[(]?\\s*["'](${libs.join("|")})(?:/|["'])`);
  const offenders = [];
  let importsSeen = 0;
  for (const path of collectSourceFiles(dir)) {
    const text = readFileSync(path, "utf-8");
    const rel = path.slice(dir.length + 1).replaceAll("\\", "/");
    if (pattern.test(text)) {
      importsSeen += 1;
      if (!allowed.some((prefix) => rel.startsWith(prefix))) {
        offenders.push(rel);
      }
    }
  }
  return { offenders, importsSeen };
}

/**
 * The entry-route closure: a BFS over each chunk's static imports.
 * listDeps maps one chunk name to the chunk names it imports; the
 * gate feeds it the dist reader below, the vitest feeds it a fake
 * chunk graph. Answers every reachable chunk name, entry included.
 */
export function walkClosure(entry, listDeps) {
  const routeSet = new Set([entry]);
  const queue = [entry];
  while (queue.length > 0) {
    for (const dep of listDeps(queue.shift())) {
      if (!routeSet.has(dep)) {
        routeSet.add(dep);
        queue.push(dep);
      }
    }
  }
  return routeSet;
}

function staticImports(name, assets) {
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

function markedChunks(assets, marker) {
  return assets.filter((name) => {
    try {
      return readFileSync(join(distAssets, name), "utf-8").includes(marker);
    } catch {
      return false;
    }
  });
}

/** One family's two checks; answers true when it failed. */
function checkFamily(family, assets, routeSet) {
  let failed = false;

  // -- 1. Source scan ------------------------------------------------------
  const { offenders, importsSeen } = scanSource(srcDir, {
    allowedPrefixes: family.allowed,
    libPackages: family.packages,
  });
  if (importsSeen === 0 && family.requireImports) {
    console.error(
      `check-import-graph: no source file imports ${family.friendly} — the scan is lying. Fix the marker list.`,
    );
    failed = true;
  }
  if (offenders.length > 0) {
    console.error(
      `check-import-graph: ${family.friendly} are imported outside ${family.home}:`,
    );
    for (const rel of offenders) {
      console.error(`  ${rel}`);
    }
    failed = true;
  }

  // -- 2. Built-output scan ------------------------------------------------
  if (family.marker === null) {
    // A family with no import yet: nothing to pin, nothing to leak.
    return failed;
  }
  const marked = markedChunks(assets, family.marker);
  if (marked.length === 0 && family.requireImports) {
    console.error(
      `check-import-graph: no built chunk carries the ${family.key} marker — the library never built. ` +
        "Run the build; if the marker string changed upstream, update the marker constant before trusting this gate.",
    );
    failed = true;
  }
  const inRoute = marked.filter((name) => routeSet.has(name));
  if (inRoute.length > 0) {
    console.error(
      `check-import-graph: ${family.key} chunks leaked into the initial route:`,
    );
    for (const name of inRoute) {
      console.error(`  ${name}`);
    }
    failed = true;
  }
  console.log(
    `check-import-graph: ${family.friendly} stay in ${marked.length} lazy chunk(s) outside the route.`,
  );
  return failed;
}

function main() {
  let failed = false;

  const assets = readdirSync(distAssets).filter((n) => n.endsWith(".js") || n.endsWith(".css"));
  let entry = null;
  try {
    const html = readFileSync(join(distDir, "index.html"), "utf-8");
    entry = /assets\/([\w.-]+\.js)/.exec(html)?.[1] ?? null;
  } catch {
    console.error("check-import-graph: cannot read index.html next to the assets.");
    process.exit(1);
  }
  if (entry === null || !assets.includes(entry)) {
    console.error("check-import-graph: entry chunk from index.html not found. Run the build first.");
    process.exit(1);
  }

  const routeSet = walkClosure(entry, (name) => staticImports(name, assets));

  const families = [
    {
      key: "xyflow",
      friendly: "the map libraries",
      home: "the lazy Map module",
      packages: LIB_PACKAGES,
      allowed: ALLOWED_PREFIXES,
      marker: LIB_MARKER,
      requireImports: true,
    },
    {
      key: "chat",
      friendly: "the chat packages",
      home: "the lazy Chat module",
      packages: CHAT_PACKAGES,
      allowed: CHAT_ALLOWED_PREFIXES,
      marker: CHAT_MARKER,
      requireImports: CHAT_REQUIRE_IMPORTS,
    },
  ];
  for (const family of families) {
    if (checkFamily(family, assets, routeSet)) {
      failed = true;
    }
  }

  if (failed) {
    process.exit(1);
  }
  console.log(
    `check-import-graph: the initial route (${routeSet.size} chunks) is clean.`,
  );
}

// Run only when executed directly; the vitest import stays inert.
const isMain = Boolean(process.argv[1]) &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isMain) {
  main();
}
