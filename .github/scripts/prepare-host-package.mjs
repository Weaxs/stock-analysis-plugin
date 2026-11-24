import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const [host, variant] = process.argv.slice(2);
assert(
  (host === "openclaw" || host === "dsh") &&
    (variant === undefined || (host === "dsh" && variant === "--unscoped")),
  "Usage: node .github/scripts/prepare-host-package.mjs <openclaw|dsh> [--unscoped (dsh only)]",
);

const root = fileURLToPath(new URL("../../", import.meta.url));
const cwd = join(root, host);
const scopedName = `@weaxs/${host}-stock-analysis`;
const packageName = variant ? "dsh-stock-analysis" : scopedName;
const dshReferences = [
  ["cordis.patch.yml", `name: "${packageName}"`],
  ["research.patch.yml", `name: '${packageName}/research-role'`],
  ["dist/client.js", `id: "${packageName}"`],
];

if (variant) {
  execFileSync("npm", ["pkg", "set", `name=${packageName}`], { cwd, stdio: "inherit" });
  for (const [file] of dshReferences) {
    const path = join(cwd, file);
    writeFileSync(path, readFileSync(path, "utf8").replaceAll(scopedName, packageName));
  }
} else {
  for (const directory of ["tools", "skills", "schemas", "templates", "strategies", "scripts"]) {
    const source = join(root, directory);
    const destination = join(cwd, directory);
    if (existsSync(source)) {
      rmSync(destination, { recursive: true, force: true });
      cpSync(source, destination, { recursive: true });
    }
  }
  rmSync(join(cwd, "scripts/sync-version.sh"), { force: true });
  cpSync(join(root, "LICENSE"), join(cwd, "LICENSE"));
}

assert(existsSync(join(cwd, "dist/index.js")), `${host}/dist/index.js is missing`);
const requiredFiles = [
  "LICENSE",
  "dist/index.js",
  "scripts/setup-python.mjs",
  "tools/requirements.txt",
  "tools/tool_schemas.json",
  ...(host === "openclaw"
    ? ["index.ts", "openclaw.plugin.json"]
    : [
        "cordis.patch.yml",
        "research.patch.yml",
        "deepseek-search.patch.yml",
        "dist/research-role.js",
        "dist/client.js",
        "dist/web/index.html",
        "dist/web/app.js",
        "dist/web/style.css",
      ]),
];

// ClawHub ships the staged OpenClaw directory, so keep the check archive elsewhere.
const packDir = mkdtempSync(join(tmpdir(), "stock-analysis-pack-"));
try {
  const [packed] = JSON.parse(
    execFileSync("npm", ["pack", "--json", "--pack-destination", packDir], {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "inherit"],
    }),
  );
  assert.equal(packed.name, packageName);
  const files = new Set(packed.files.map(({ path }) => path));
  for (const file of requiredFiles) {
    assert(files.has(file), `${file} is missing from ${packed.filename}`);
  }
  if (host === "dsh") {
    for (const [file, expected] of dshReferences) {
      const contents = execFileSync("tar", ["-xOf", join(packDir, packed.filename), `package/${file}`], {
        encoding: "utf8",
      });
      assert(contents.includes(expected), `${file} is missing ${expected}`);
      if (variant) assert(!contents.includes(scopedName), `${file} still references ${scopedName}`);
    }
  }
  console.log(`Verified ${packed.filename}`);
} finally {
  rmSync(packDir, { recursive: true, force: true });
}
