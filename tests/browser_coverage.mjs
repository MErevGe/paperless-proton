import fs from "node:fs/promises";
import coverage from "istanbul-lib-coverage";
import v8ToIstanbul from "v8-to-istanbul";

const entries = JSON.parse(
  await fs.readFile(".local/verification/browser-coverage.json", "utf8"),
);
const map = coverage.createCoverageMap({});
for (const entry of entries) {
  const converter = v8ToIstanbul("bridge_container/static/app.js");
  await converter.load();
  converter.applyCoverage(entry.functions);
  map.merge(converter.toIstanbul());
}
const summary = map.getCoverageSummary().toJSON();
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
