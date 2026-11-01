/// <reference types="vitest/config" />
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Serves the app config from its source-of-truth location during local dev, mirroring production
// where sync-frontend.sh publishes it at the site origin root.
function serveAppConfig(): Plugin {
  return {
    name: "serve-app-config",
    configureServer(server) {
      server.middlewares.use("/app.json", (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.end(readFileSync(fileURLToPath(new URL("../config/app.json", import.meta.url))));
      });
    },
  };
}

// One git command's trimmed output, run in the checkout the build runs in.
function git(...args: string[]): string {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

// The version the app names under its title: the release the checkout descends from, tagged
// vMAJOR.MINOR on GitHub, and how many commits main has moved past it. It is read from git when
// the build runs, so a checkout without the release tags fails the build rather than ship a
// version that names no release.
function appVersion(): string {
  const release = git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*");
  const commitsSince = git("rev-list", "--count", `${release}..HEAD`);
  return `${release.slice(1)}.${commitsSince}`;
}

// The date of the commit the build was made from, DD/MM/YYYY, so the version line names when the
// code it describes was written.
function appCommitDate(): string {
  return git("log", "-1", "--format=%cd", "--date=format:%d/%m/%Y");
}

export default defineConfig({
  plugins: [react(), serveAppConfig()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
    __APP_COMMIT_DATE__: JSON.stringify(appCommitDate()),
  },
  // Cognito's registered local callback URL is http://localhost:8000/ — the dev server must
  // stay on that port for local sign-in to work.
  server: { port: 8000 },
  preview: { port: 8000 },
  build: { target: "es2022" },
  test: {
    environment: "jsdom",
    // Globals are on so Testing Library's between-test DOM cleanup can self-register.
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
