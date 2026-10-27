import { defaultExclude, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Spec D.6 (UI SPA program): the SPA builds into the Python package as
// static files and serves under /app — base is load-bearing (JS/CSS URLs
// resolve to /app/assets/...). The dev server proxies /api to the
// running corpus-kb server; CORS stays untouched.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../src/corpus_kb/ui_dist",
    emptyOutDir: true,
    sourcemap: false,
    // Raw-minified advisory only: the antd shell is ~584 kB raw but
    // ~190 kB gzipped. The REAL budget gate is the gzip cap (350 kB for
    // the initial route, set from the Task 3 bundle-probe data: shell
    // 191.7 kB + probe screen 131.4 kB = 323 kB); its check script lands
    // with Task 9. This limit just keeps the advisory from drowning the
    // build log until then.
    chunkSizeWarningLimit: 600,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8010",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // The Playwright smoke (e2e/) runs in its own runner, not vitest.
    exclude: ["e2e/**", ...defaultExclude],
  },
});
