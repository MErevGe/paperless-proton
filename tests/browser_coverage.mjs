import fs from "node:fs/promises";
import path from "node:path";
import coverage from "istanbul-lib-coverage";
import v8ToIstanbul from "v8-to-istanbul";

const entries = JSON.parse(
  await fs.readFile(".local/verification/browser-coverage.json", "utf8"),
);
const map = coverage.createCoverageMap({});
const sources = ["app.js", "theme.js"];
for (const entry of entries) {
  const source = new URL(entry.url).pathname.slice(1);
  if (!sources.includes(source)) continue;
  const converter = v8ToIstanbul(
    path.resolve("bridge_container/static", source),
  );
  await converter.load();
  converter.applyCoverage(entry.functions);
  map.merge(converter.toIstanbul());
}
const summary = map.getCoverageSummary().toJSON();
for (const source of sources) {
  const file = path.resolve("bridge_container/static", source);
  if (!map.files().includes(file)) {
    console.error(`Missing browser coverage: ${source}`);
    process.exitCode = 1;
    continue;
  }
  const result = map.fileCoverageFor(file).toSummary().toJSON();
  console.log(
    `${source}: ${result.lines.pct}% lines; ${result.branches.pct}% branches`,
  );
  if (result.lines.pct < 90 || result.branches.pct < 80) process.exitCode = 1;
}
await fs.writeFile(
  ".local/verification/browser-coverage-summary.json",
  JSON.stringify(summary, null, 2),
);
console.log(
  `Browser line coverage: ${summary.lines.pct}%; branch coverage: ${summary.branches.pct}%`,
);
if (!entries.length || summary.lines.pct < 90 || summary.branches.pct < 80) {
  process.exitCode = 1;
}
