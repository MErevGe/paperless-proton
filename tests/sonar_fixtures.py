import copy
from unittest.mock import Mock

from ci.sonar_api import Api, SyncError
from ci.sonar_findings import BOT_ID, LABELS
from ci.sonar_sync import Synchronizer

PROJECT = "example_project"
REPOSITORY = "example/project"
REVISION = "a" * 40


def issue(key="issue-1", **changes):
    return {
        "key": key,
        "project": PROJECT,
        "component": PROJECT + ":src/example.py",
        "rule": "python:S1234",
        "message": "Check this code.",
        "line": 12,
        "issueStatus": "OPEN",
        "impacts": [{"softwareQuality": "MAINTAINABILITY", "severity": "LOW"}],
        **changes,
    }


def hotspot(key="hotspot-1", **changes):
    return {
        "key": key,
        "project": PROJECT,
        "component": PROJECT + ":src/example.py",
        "ruleKey": "python:S5678",
        "message": "Review this security-sensitive code.",
        "status": "TO_REVIEW",
        "vulnerabilityProbability": "HIGH",
        **changes,
    }


def ticket(finding, number=1, **changes):
    return {
        "number": number,
        "title": finding.title,
        "body": finding.body,
        "state": "open",
        "state_reason": None,
        "labels": [{"name": "sonarqube"}, {"name": "sonarqube:" + finding.kind}],
        "user": {"id": BOT_ID, "type": "Bot", "login": "github-actions[bot]"},
        **changes,
    }


class ServiceFixture:
    def __init__(self):
        self.issues = [issue()]
        self.hotspots = [hotspot()]
        self.tickets = []
        self.labels = []
        self.revision = REVISION
        self.analysis_revision = REVISION
        self.analysis_key = "analysis-1"
        self.branches = [{"name": "main", "isMain": True}]
        self.reads = []
        self.writes = []
        self.fail_path = ""

    def request(self, method, path, *, query=None, body=None):
        if path == self.fail_path:
            raise SyncError("The test API is unavailable.")
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body)))
            if path.endswith("/labels"):
                self.labels.append(body)
            elif method == "POST":
                if "state" in body or "state_reason" in body:
                    raise AssertionError("Creating an issue cannot set its state.")
                self.tickets.append(
                    {
                        **body,
                        "number": len(self.tickets) + 1,
                        "state": "open",
                        "state_reason": None,
                        "labels": [{"name": label} for label in body["labels"]],
                        "user": {"id": BOT_ID, "type": "Bot"},
                    }
                )
                return copy.deepcopy(self.tickets[-1])
            else:
                target = next(
                    item for item in self.tickets if item["number"] == int(path.rsplit("/", 1)[1])
                )
                target.update(body)
                if "labels" in body:
                    target["labels"] = [{"name": label} for label in body["labels"]]
            return {"ok": True}
        self.reads.append((path, copy.deepcopy(query)))
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.revision}}
        if path == "/api/project_branches/list":
            return {"branches": self.branches}
        if path == "/api/project_analyses/search":
            return {"analyses": [{"key": self.analysis_key, "revision": self.analysis_revision}]}
        if path in {"/api/issues/search", "/api/hotspots/search"}:
            field = "issues" if path == "/api/issues/search" else "hotspots"
            rows = self.issues if field == "issues" else self.hotspots
            page, size = int(query["p"]), int(query["ps"])
            return copy.deepcopy(
                {
                    "paging": {"pageIndex": page, "pageSize": size, "total": len(rows)},
                    field: rows[(page - 1) * size : page * size],
                }
            )
        if path in {f"/repos/{REPOSITORY}/issues", f"/repos/{REPOSITORY}/labels"}:
            rows = self.tickets if path.endswith("/issues") else self.labels
            page, size = int(query["page"]), int(query["per_page"])
            return copy.deepcopy(rows[(page - 1) * size : page * size])
        if path.startswith(f"/repos/{REPOSITORY}/issues/"):
            return copy.deepcopy(
                next(item for item in self.tickets if item["number"] == int(path.rsplit("/", 1)[1]))
            )
        raise AssertionError(f"Unexpected fixture request: {method} {path}")

    def synchronizer(self):
        sonar, github = Mock(spec=Api), Mock(spec=Api)
        sonar.request.side_effect = self.request
        github.request.side_effect = self.request
        return Synchronizer(sonar, github, PROJECT, REPOSITORY, REVISION)

    def existing_labels(self):
        self.labels = [{"name": label} for label in LABELS]
