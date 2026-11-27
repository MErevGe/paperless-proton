import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ci.sonar_api import SyncError, array_value, identifier, integer_value, object_value, text_value

END_MARKER = "<!-- /sonar-sync -->"
BOT_ID = 41898282
LABELS = {
    "sonarqube": "Findings tracked from SonarQube Cloud",
    "sonarqube:issue": "Code quality and security issues",
    "sonarqube:hotspot": "Security review tasks; not confirmed vulnerabilities",
}


def markdown(value: str) -> str:
    value = html.escape(value, quote=False).replace("@", "@\u200b")
    value = " ".join(value.split())
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)
    encoded = escaped.encode("utf-8")
    return (
        encoded[:5997].decode("utf-8", errors="ignore") + "..." if len(encoded) > 6000 else escaped
    )


@dataclass(frozen=True)
class Finding:
    project: str
    kind: str
    key: str
    message: str
    component: str
    line: int | None
    rule: str
    status: str
    resolution: str
    impacts: tuple[tuple[str, str], ...]
    fixed: bool

    @property
    def identity(self) -> tuple[str, str]:
        return self.kind, self.key

    @property
    def marker(self) -> str:
        return f"<!-- sonar-sync:v1 {self.project} {self.kind} {self.key} -->"

    @property
    def title(self) -> str:
        message = " ".join(self.message.split()).replace("@", "@\u200b")
        return f"[SonarQube {self.kind}] {message}"[:240]

    @property
    def body(self) -> str:
        page = "project/issues" if self.kind == "issue" else "security_hotspots"
        parameter = "issues" if self.kind == "issue" else "hotspots"
        link = (
            "https://sonarcloud.io/"
            + page
            + "?"
            + urlencode({"id": self.project, parameter: self.key})
        )
        location = self.component.removeprefix(self.project + ":")
        if self.line is not None:
            location += f":{self.line}"
        impact = "; ".join(f"{quality}: {severity}" for quality, severity in self.impacts)
        lines = [
            self.marker,
            f"{markdown(self.message)}\n",
            f"- Kind: {self.kind}",
            f"- Impact / review priority: {markdown(impact)}",
            f"- Status: {markdown(self.status)}",
            f"- Resolution: {markdown(self.resolution or 'Unresolved')}",
            f"- Location: {markdown(location)}",
            f"- Rule: {markdown(self.rule)}",
            f"- Finding ID: {markdown(self.key)}",
            f"\n[Open in SonarQube Cloud]({link})\n",
            "This section is synchronized from the main branch. Add personal notes in comments.",
        ]
        if self.kind == "hotspot":
            lines.append(
                "Security hotspots require review; they are not confirmed vulnerabilities."
            )
        return "\n".join([*lines, END_MARKER])


def parse_finding(raw: Any, project: str, kind: str) -> Finding:
    data = object_value(raw)
    if data.get("project") != project:
        raise SyncError("A finding belongs to a different SonarQube project.")
    key = identifier(data.get("key"))
    component = text_value(data.get("component"))
    if component != project and not component.startswith(project + ":"):
        raise SyncError("A finding has an unexpected component.")
    line = data.get("line")
    if line is not None:
        line = integer_value(line, 1)
    raw_resolution = data.get("resolution")
    resolution = "Unresolved" if raw_resolution is None else text_value(raw_resolution)
    if kind == "issue":
        status = text_value(data.get("issueStatus", data.get("status")))
        impacts = tuple(
            (
                text_value(object_value(item).get("softwareQuality")),
                text_value(item.get("severity")),
            )
            for item in array_value(data.get("impacts", []))
        )
        if not impacts:
            impacts = ((text_value(data.get("type")), text_value(data.get("severity"))),)
        rule = text_value(data.get("rule"))
        fixed = status == "FIXED" or (status in {"CLOSED", "RESOLVED"} and resolution == "FIXED")
    else:
        status = text_value(data.get("status"))
        impacts = (("Security review priority", text_value(data.get("vulnerabilityProbability"))),)
        rule = text_value(data.get("ruleKey", data.get("securityCategory")))
        fixed = status == "REVIEWED" and resolution == "FIXED"
    return Finding(
        project,
        kind,
        key,
        text_value(data.get("message")),
        component,
        line,
        rule,
        status,
        resolution,
        tuple(sorted(set(impacts))),
        fixed,
    )


@dataclass(frozen=True)
class Ticket:
    number: int
    title: str
    body: str
    state: str
    labels: frozenset[str]
    identity: tuple[str, str]
    start: int
    end: int


def managed_tickets(items: list[Any], project: str) -> dict[tuple[str, str], Ticket]:
    result = {}
    prefix = f"<!-- sonar-sync:v1 {project} "
    pattern = re.compile(re.escape(prefix) + r"(issue|hotspot) ([a-zA-Z0-9_.:-]{1,256}) -->")
    for raw in items:
        data = object_value(raw)
        if "pull_request" in data:
            continue
        user = object_value(data.get("user"))
        if user.get("id") != BOT_ID or user.get("type") != "Bot":
            continue
        body = data.get("body") or ""
        if not isinstance(body, str):
            raise SyncError("A GitHub issue has an invalid body.")
        if prefix not in body:
            continue
        matches = list(pattern.finditer(body))
        if len(matches) != 1 or body.count(prefix) != 1 or body.count(END_MARKER) != 1:
            raise SyncError("A managed issue has invalid synchronization markers.")
        match = matches[0]
        end = body.index(END_MARKER)
        if end < match.end():
            raise SyncError("A managed issue has reversed synchronization markers.")
        identity = match[1], match[2]
        if identity in result:
            raise SyncError("Multiple GitHub issues refer to the same SonarQube finding.")
        state = text_value(data.get("state"))
        if state not in {"open", "closed"}:
            raise SyncError("A GitHub issue has an invalid state.")
        result[identity] = Ticket(
            integer_value(data.get("number"), 1),
            text_value(data.get("title")),
            body,
            state,
            frozenset(
                text_value(object_value(label).get("name"))
                for label in array_value(data.get("labels"))
            ),
            identity,
            match.start(),
            end + len(END_MARKER),
        )
    return result


@dataclass(frozen=True)
class Change:
    action: str
    identity: tuple[str, str]
    number: int | None
    payload: dict[str, Any]
    expected: dict[str, Any] | None = None


def plan_changes(findings: list[Finding], tickets: dict[tuple[str, str], Ticket]) -> list[Change]:
    changes = []
    seen = set()
    for finding in sorted(findings, key=lambda item: item.identity):
        if finding.identity in seen:
            raise SyncError("The SonarQube response contains duplicate finding IDs.")
        seen.add(finding.identity)
        ticket = tickets.get(finding.identity)
        if ticket is None and finding.fixed:
            continue
        labels = {"sonarqube", f"sonarqube:{finding.kind}"}
        payload: dict[str, Any] = {"title": finding.title, "body": finding.body}
        if ticket is None:
            payload["labels"] = sorted(labels)
            changes.append(Change("create", finding.identity, None, payload))
            continue
        payload["body"] = ticket.body[: ticket.start] + finding.body + ticket.body[ticket.end :]
        if len(payload["body"].encode("utf-8")) > 65000:
            raise SyncError("A managed issue exceeds the body size limit; shorten its notes.")
        payload["labels"] = sorted((ticket.labels - LABELS.keys()) | labels)
        desired_state = "closed" if finding.fixed else "open"
        action = "update"
        if desired_state != ticket.state:
            action = "close" if finding.fixed else "reopen"
            payload.update(
                state=desired_state, state_reason="completed" if finding.fixed else "reopened"
            )
        if (
            payload["title"] == ticket.title
            and payload["body"] == ticket.body
            and frozenset(payload["labels"]) == ticket.labels
            and desired_state == ticket.state
        ):
            continue
        expected = {
            "title": ticket.title,
            "body": ticket.body,
            "state": ticket.state,
            "labels": sorted(ticket.labels),
        }
        changes.append(Change(action, finding.identity, ticket.number, payload, expected))
    return sorted(changes, key=lambda change: change.action == "close")
