import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const exec = promisify(execFile);
const root = fileURLToPath(new URL("../", import.meta.url));
const source = "export function example() {\n  return 42;\n}\nexample();\n";

async function fixture(t, sources, count = 1) {
  const directory = await fs.mkdtemp(
    path.join(os.tmpdir(), "browser-coverage-"),
  );
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  await fs.mkdir(path.join(directory, "bridge_container/static"), {
    recursive: true,
  });
  await fs.mkdir(path.join(directory, ".local/verification"), {
    recursive: true,
  });
  for (const name of ["app.js", "theme.js"]) {
    await fs.writeFile(
      path.join(directory, "bridge_container/static", name),
      source,
    );
  }
  await fs.copyFile(
    path.join(root, "tests/browser_coverage.mjs"),
    path.join(directory, "browser_coverage.mjs"),
  );
  await fs.symlink(
    path.join(root, "node_modules"),
    path.join(directory, "node_modules"),
    "dir",
  );
  await fs.writeFile(
    path.join(directory, ".local/verification/browser-coverage.json"),
    JSON.stringify(
      sources.map((name) => ({
        url: `https://localhost/${name}`,
        functions: [
          {
            functionName: "",
            isBlockCoverage: true,
            ranges: [{ startOffset: 0, endOffset: source.length, count }],
          },
        ],
      })),
    ),
  );
  return directory;
}

test("LCOV preserves hits and uses paths relative to the checkout", async (t) => {
  const cwd = await fixture(t, ["app.js", "theme.js", "unrelated.js"]);
  await exec(process.execPath, ["browser_coverage.mjs"], { cwd });
  const lcov = await fs.readFile(
    path.join(cwd, ".local/verification/lcov.info"),
    "utf8",
  );
  assert.deepEqual(lcov.match(/^SF:.*$/gm), [
    "SF:bridge_container/static/app.js",
    "SF:bridge_container/static/theme.js",
  ]);
  assert.equal(lcov.match(/^end_of_record$/gm).length, 2);
  assert.match(lcov, /^DA:2,1$/m);
  assert.match(lcov, /^BRDA:1,0,0,1$/m);
  assert.match(lcov, /^BRF:1$/m);
  assert.match(lcov, /^BRH:1$/m);
  assert.ok(!lcov.includes(cwd));
  assert.ok(!lcov.includes("unrelated.js"));
});

test("uncovered code fails the gate and retains zero hits in LCOV", async (t) => {
  const cwd = await fixture(t, ["app.js", "theme.js"], 0);
  await assert.rejects(
    exec(process.execPath, ["browser_coverage.mjs"], { cwd }),
    {
      code: 1,
    },
  );
  const lcov = await fs.readFile(
    path.join(cwd, ".local/verification/lcov.info"),
    "utf8",
  );
  assert.match(lcov, /^DA:2,0$/m);
  assert.match(lcov, /^BRH:0$/m);
});

test("missing coverage for a browser source fails the gate", async (t) => {
  const cwd = await fixture(t, ["app.js"]);
  await assert.rejects(
    exec(process.execPath, ["browser_coverage.mjs"], { cwd }),
    {
      code: 1,
      stderr: /Missing browser coverage: theme.js/,
    },
  );
});

test("empty coverage cannot pass the gate", async (t) => {
  const cwd = await fixture(t, []);
  await assert.rejects(
    exec(process.execPath, ["browser_coverage.mjs"], { cwd }),
    {
      code: 1,
    },
  );
});
