// Builds the UI into one self-contained HTML file and installs it as the shell agentlint ships
// (src/agentlint/ui/shell.html). The shell carries a hash of the sources it was built from, which a
// Python test recomputes, so the shipped shell can't drift from this code (docs/tech_impl/11-ui.md).
import { createHash } from "node:crypto";
import { copyFileSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

const UI = __dirname;
const SHELL = join(UI, "..", "src", "agentlint", "ui", "shell.html");
const ROOT_FILES = ["index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts"];

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  });
}

/** sha256 over each built-from file: its relative path, a NUL, its bytes, a NUL — tests and dotfiles
 * (.DS_Store) excluded. tests/test_ui_shell.py computes the same thing. */
export function sourceHash(): string {
  const paths = [...ROOT_FILES.map((f) => join(UI, f)), ...filesUnder(join(UI, "src"))]
    .map((path) => relative(UI, path).split(sep).join("/"))
    .filter((path) => !/\.test\.tsx?$/.test(path) && !path.split("/").some((part) => part.startsWith(".")))
    .sort();
  const hash = createHash("sha256");
  for (const path of paths) {
    hash.update(path + "\0");
    hash.update(readFileSync(join(UI, path)));
    hash.update("\0");
  }
  return "sha256:" + hash.digest("hex");
}

function agentlintShell(): Plugin {
  return {
    name: "agentlint-shell",
    transformIndexHtml: (html) =>
      html.replace("<head>", `<head>\n    <meta name="agentlint-ui-source" content="${sourceHash()}" />`),
    closeBundle: () => copyFileSync(join(UI, "dist", "index.html"), SHELL),
  };
}

export default defineConfig({
  plugins: [react(), viteSingleFile(), agentlintShell()],
});
