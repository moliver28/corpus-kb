import { defaultExclude, defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Spec D.6 (UI SPA program): the SPA builds into the Python package as
// static files and serves under /app — base is load-bearing (JS/CSS URLs
// resolve to /app/assets/...). The dev server proxies /api to the
// running corpus-kb server; CORS stays untouched.
const BASE = "/app/";

// The chrome fonts load up front (Plex Sans + Plex Mono, latin subset);
// the evidence serif rides in the Ask stage chunk and is never
// preloaded. Only the weights the app renders (400 and 600) go out
// as preloads. The 500 faces ship as @font-face declarations, but no
// rule asks for weight 500, so the browser never fetches those files.
// Emitted font names carry a build hash, so the preload
// links are injected from the build bundle, not hard-coded. They ride
// at the end of the head: <meta charset> always comes first.
const SHELL_FONT_RE =
  /ibm-plex-(sans|mono)-latin-(400|600)-normal-[A-Za-z0-9_-]+\.woff2$/;

function fontPreloads(): Plugin {
  return {
    name: "ckb-font-preloads",
    apply: "build",
    transformIndexHtml(_html, ctx) {
      if (!ctx.bundle) {
        return [];
      }
      return Object.keys(ctx.bundle)
        .filter((name) => SHELL_FONT_RE.test(name))
        .map((name) => ({
          tag: "link",
          attrs: {
            rel: "preload",
            href: `${BASE}${name}`,
            as: "font",
            type: "font/woff2",
            crossorigin: "",
          },
          injectTo: "head",
        }));
    },
  };
}

export default defineConfig({
  plugins: [react(), fontPreloads()],
  base: BASE,
  build: {
    outDir: "../src/corpus_kb/ui_dist",
    emptyOutDir: true,
    sourcemap: false,
    // Raw-minified advisory only: the REAL budget gate is the gzip cap
    // (check-bundle.mjs, 350 kB initial route / per chunk). The
    // grouped antd-core vendor chunk is ~700 kB raw by design, so the
    // advisory sits above it and stays quiet.
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        // Honest vendor names: today the auto chunks lie (the chunk
        // named after src/strings carries React core; the one named
        // after the useOp hook carries antd core). Each group names
        // its vendor family truthfully. [\\/] also matches Windows
        // module ids (rolldown's own guidance).
        advancedChunks: {
          groups: [
            { name: "react-core", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            {
              name: "react-router",
              test: /node_modules[\\/](react-router|@remix-run[\\/]router)[\\/]/,
            },
            { name: "tanstack-query", test: /node_modules[\\/]@tanstack[\\/]/ },
            {
              name: "antd-core",
              // Shared antd only: a component one stage uses alone
              // (Upload, Drawer) stays lazy in that stage's chunk, so
              // the route budget survives new stage surfaces.
              minShareCount: 2,
              test: /node_modules[\\/](@ant-design|@rc-component|antd|rc-[a-z-]+)[\\/]/,
            },
          ],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8010",
    },
  },
  test: {
    // Runner mode (the one place it is written down): the suite runs on
    // vitest's standing defaults — default worker pool at full host
    // parallelism, one fresh jsdom per test file. Nothing is overridden.
    // Under that load some page tests can hit the 5 s per-test timeout,
    // a VARYING set each run (Wave 2 saw 7 then 12 files under 36
    // workers; it reproduces on the pre-wave base too, so it is host
    // load, not code). Every failing file passes in isolation; a flaky
    // run re-runs with `--maxWorkers=2` (the Wave 2 clean mode) before
    // anyone hunts for a code cause.
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // The suite runs many jsdom files in parallel workers; the default
    // 5 s per-test budget tips over under that load as the suite grows
    // (finder waits already run wide). The honest trade: a missing
    // element now surfaces only after the finder's full 15 s budget.
    testTimeout: 15_000,
    // The Playwright smoke (e2e/) runs in its own runner, not vitest.
    exclude: ["e2e/**", ...defaultExclude],
  },
});
