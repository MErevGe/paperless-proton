import json
from pathlib import Path

totals = json.loads(Path("coverage.json").read_text())["totals"]
line_coverage = totals["covered_lines"] / totals["num_statements"] * 100
branch_coverage = totals["covered_branches"] / totals["num_branches"] * 100
print(f"Line coverage: {line_coverage:.2f}%; branch coverage: {branch_coverage:.2f}%")
if line_coverage < 90 or branch_coverage < 80:
    raise SystemExit("Coverage thresholds not met")
