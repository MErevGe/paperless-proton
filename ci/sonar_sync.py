import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ci.sonar_api import (
    Api,
    SyncError,
    array_value,
    identifier,
    integer_value,
    object_value,
    text_value,
)
from ci.sonar_findings import LABELS, Change, Finding, managed_tickets, parse_finding, plan_changes

PAGE_SIZE = 100
MAX_ITEMS = 10000


class Synchronizer:
    def __init__(
        self, sonar: Api, github: Api, project: str, repository: str, revision: str
    ) -> None:
        self.sonar = sonar
        self.github = github
        self.project = identifier(project)
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+", repository):
            raise SyncError("Use an owner/repository GitHub repository name.")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise SyncError("Use the full main branch commit SHA.")
        self.repository = "/repos/" + repository
        self.revision = revision

    def analysis(self) -> str:
        reference = object_value(
            self.github.request("GET", self.repository + "/git/ref/heads/main")
        )
        if object_value(reference.get("object")).get("sha") != self.revision:
            raise SyncError("The triggering commit is no longer the current main branch.")
        branches = object_value(
            self.sonar.request("GET", "/api/project_branches/list", query={"project": self.project})
        )
        main = [
            object_value(branch).get("name")
            for branch in array_value(branches.get("branches"))
            if object_value(branch).get("isMain") is True
        ]
        if main != ["main"]:
            raise SyncError("The SonarQube main branch must be named main.")
        data = object_value(
            self.sonar.request(
                "GET",
                "/api/project_analyses/search",
                query={"project": self.project, "branch": "main", "ps": "1", "p": "1"},
            )
        )
        analyses = array_value(data.get("analyses"))
        if not analyses or object_value(analyses[0]).get("revision") != self.revision:
            raise SyncError("No completed SonarQube main analysis matches the triggering commit.")
        return identifier(analyses[0].get("key"))

    def sonar_pages(self, kind: str) -> list[Finding]:
        field = "issues" if kind == "issue" else "hotspots"
        query = {"componentKeys": self.project, "branch": "main"}
        if kind == "hotspot":
            query = {"projectKey": self.project}
        query["ps"] = str(PAGE_SIZE)
        findings: list[Finding] = []
        total: int | None = None
        for page in range(1, MAX_ITEMS // PAGE_SIZE + 1):
            data = object_value(
                self.sonar.request("GET", f"/api/{field}/search", query={**query, "p": str(page)})
            )
            paging = object_value(data.get("paging"))
            current_total = integer_value(paging.get("total"))
            if current_total > MAX_ITEMS:
                raise SyncError("SonarQube exceeds the 10,000 findings per-kind API safety limit.")
            if total is None:
                total = current_total
            if (
                total != current_total
                or integer_value(paging.get("pageIndex"), 1) != page
                or integer_value(paging.get("pageSize"), 1) != PAGE_SIZE
            ):
                raise SyncError("SonarQube pagination changed; no complete snapshot is available.")
            entries = array_value(data.get(field))
            if len(entries) != min(PAGE_SIZE, total - len(findings)):
                raise SyncError("SonarQube returned an incomplete page.")
            findings.extend(parse_finding(item, self.project, kind) for item in entries)
            if len(findings) == total:
                return findings
        raise SyncError("SonarQube pagination did not finish.")

    def github_pages(self, endpoint: str) -> list[Any]:
        items: list[Any] = []
        for page in range(1, MAX_ITEMS // PAGE_SIZE + 2):
            entries = array_value(
                self.github.request(
                    "GET",
                    self.repository + endpoint,
                    query={
                        "state": "all",
                        "sort": "created",
                        "direction": "asc",
                        "per_page": str(PAGE_SIZE),
                        "page": str(page),
                    },
                )
            )
            if len(entries) > PAGE_SIZE or len(items) + len(entries) > MAX_ITEMS:
                raise SyncError("GitHub exceeds the 10,000 items API safety limit.")
            items.extend(entries)
            if len(entries) < PAGE_SIZE:
                return items
        raise SyncError("GitHub pagination did not finish.")

    def prepare(self) -> tuple[str, list[Change], set[str]]:
        analysis = self.analysis()
        findings = self.sonar_pages("issue") + self.sonar_pages("hotspot")
        tickets = managed_tickets(self.github_pages("/issues"), self.project)
        changes = plan_changes(findings, tickets)
        labels = {
            text_value(object_value(item).get("name")) for item in self.github_pages("/labels")
        }
        if self.analysis() != analysis:
            raise SyncError("The SonarQube analysis changed during synchronization.")
        return analysis, changes, LABELS.keys() - labels if changes else set()

    def require_analysis(self, analysis: str) -> None:
        if self.analysis() != analysis:
            raise SyncError("The SonarQube analysis changed before applying the plan.")

    def apply(self, analysis: str, changes: list[Change], missing_labels: set[str]) -> None:
        self.require_analysis(analysis)
        for label in sorted(missing_labels):
            self.require_analysis(analysis)
            self.github.request(
                "POST",
                self.repository + "/labels",
                body={
                    "name": label,
                    "color": "4c1d95",
                    "description": LABELS[label],
                },
            )
            time.sleep(1)
        for change in changes:
            path = self.repository + "/issues"
            if change.number is not None:
                path += f"/{change.number}"
                current = object_value(self.github.request("GET", path))
                managed = managed_tickets([current], self.project).get(change.identity)
                state = {
                    "title": current.get("title"),
                    "body": current.get("body"),
                    "state": current.get("state"),
                    "labels": sorted(
                        text_value(object_value(label).get("name"))
                        for label in array_value(current.get("labels"))
                    ),
                }
                if managed is None or state != change.expected:
                    raise SyncError(
                        "A GitHub issue changed after preview; rerun to preserve edits."
                    )
            self.require_analysis(analysis)
            self.github.request(
                "POST" if change.number is None else "PATCH", path, body=change.payload
            )
            time.sleep(1)


def plan_path(requested: Path) -> Path:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        temporary = os.environ.get("RUNNER_TEMP")
        if not temporary:
            raise SyncError("RUNNER_TEMP is required for workflow reports.")
        root = Path(temporary).resolve() / "sonar-sync"
    else:
        root = Path.cwd() / ".local" / "sonar-sync"
    filename = requested.name
    if (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}\.json", filename)
        or requested.resolve().parent != root
    ):
        raise SyncError("Write JSON plans only in .local/sonar-sync or RUNNER_TEMP/sonar-sync.")
    return root / filename


def write_plan(
    path: Path, analysis: str, revision: str, changes: list[Change], labels: set[str]
) -> None:
    destination = plan_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "analysis": analysis,
        "revision": revision,
        "create_labels": sorted(labels),
        "changes": [asdict(change) for change in changes],
    }
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(data, output, ensure_ascii=True, indent=2)
        output.write("\n")


def summary(changes: list[Change], apply: bool) -> str:
    lines = ["## SonarQube findings", "", "Mode: " + ("apply" if apply else "preview"), ""]
    for action in ("create", "update", "reopen", "close"):
        lines.append(f"- {action.capitalize()}: {sum(item.action == action for item in changes)}")
    lines += ["", "See the sonar-issue-plan artifact for details.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize all SonarQube findings to GitHub issues."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes in the main workflow (default: preview only).",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        required=True,
        help="JSON plan in .local/sonar-sync (RUNNER_TEMP/sonar-sync in Actions).",
    )
    args = parser.parse_args()
    try:
        if args.apply and (
            os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
        ):
            raise SyncError("Apply is only supported in the main branch GitHub Actions workflow.")
        sync = Synchronizer(
            Api("sonarcloud.io", os.environ.get("SONAR_TOKEN", "")),
            Api("api.github.com", os.environ.get("GH_TOKEN", "")),
            os.environ.get("SONAR_PROJECT_KEY", ""),
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("SONAR_REVISION", ""),
        )
        analysis, changes, labels = sync.prepare()
        write_plan(args.plan, analysis, sync.revision, changes, labels)
        if args.apply:
            sync.apply(analysis, changes, labels)
        report = summary(changes, args.apply)
        if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary_file).open("a", encoding="utf-8") as output:
                output.write(report)
        print(report)
        return 0
    except SyncError as error:
        print(f"SonarQube sync stopped: {error}", file=sys.stderr)
    except OSError:
        print("SonarQube sync stopped: could not write the local report.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
