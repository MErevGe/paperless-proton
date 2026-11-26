import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sonar_fixtures import PROJECT, REPOSITORY, REVISION, ServiceFixture, hotspot, issue, ticket

from ci.sonar_api import SyncError, array_value, identifier, integer_value, object_value, text_value
from ci.sonar_findings import END_MARKER, LABELS, managed_tickets, parse_finding, plan_changes
from ci.sonar_sync import Synchronizer, main, summary, write_plan


class FindingTests(unittest.TestCase):
    def test_null_resolution_is_unresolved_and_other_invalid_values_are_rejected(self):
        for kind, factory in (("issue", issue), ("hotspot", hotspot)):
            with self.subTest(kind=kind):
                finding = parse_finding(factory(resolution=None), PROJECT, kind)
                self.assertEqual(finding.resolution, "Unresolved")
                self.assertFalse(finding.fixed)
                self.assertEqual(plan_changes([finding], {})[0].action, "create")
                for invalid in ("", False, 0, [], {}):
                    with self.subTest(value=invalid), self.assertRaises(SyncError):
                        parse_finding(factory(resolution=invalid), PROJECT, kind)

    def test_all_priorities_qualities_and_statuses_are_tracked(self):
        findings = []
        for quality in ("SECURITY", "RELIABILITY", "MAINTAINABILITY"):
            for severity in ("INFO", "LOW", "MEDIUM", "HIGH", "BLOCKER"):
                for status in ("OPEN", "CONFIRMED", "ACCEPTED", "DEFERRED", "FALSE_POSITIVE"):
                    data = issue(
                        f"{quality}-{severity}-{status}",
                        issueStatus=status,
                        impacts=[{"softwareQuality": quality, "severity": severity}],
                    )
                    findings.append(parse_finding(data, PROJECT, "issue"))
        for priority in ("LOW", "MEDIUM", "HIGH"):
            for status, resolution in (
                ("TO_REVIEW", "Unresolved"),
                ("REVIEWED", "SAFE"),
                ("REVIEWED", "ACKNOWLEDGED"),
            ):
                data = hotspot(
                    f"{priority}-{resolution}",
                    vulnerabilityProbability=priority,
                    status=status,
                    resolution=resolution,
                )
                findings.append(parse_finding(data, PROJECT, "hotspot"))
        changes = plan_changes(findings, {})
        self.assertEqual(len(changes), 84)
        self.assertTrue(all(change.action == "create" for change in changes))
        for finding, change in zip(
            sorted(findings, key=lambda item: item.identity), changes, strict=True
        ):
            self.assertIn(finding.status.replace("_", "\\_"), change.payload["body"])
            self.assertIn(finding.impacts[0][1], change.payload["body"])

    def test_legacy_issues_and_hotspot_category_fallback(self):
        data = issue(type="BUG", severity="MAJOR", status="RESOLVED", resolution="FIXED")
        del data["impacts"], data["issueStatus"]
        finding = parse_finding(data, PROJECT, "issue")
        self.assertTrue(finding.fixed)
        self.assertEqual(finding.impacts, (("BUG", "MAJOR"),))
        self.assertEqual(plan_changes([finding], {}), [])
        data = hotspot(securityCategory="others", status="REVIEWED", resolution="FIXED")
        del data["ruleKey"]
        finding = parse_finding(data, PROJECT, "hotspot")
        self.assertEqual(finding.rule, "others")
        self.assertTrue(finding.fixed)
        self.assertIn("not confirmed vulnerabilities", finding.body)

    def test_remote_content_is_escaped_and_mentions_are_neutralized(self):
        finding = parse_finding(
            issue(
                message="<img src=x> @example [click](https://example.invalid)\n#17 " + END_MARKER,
                component=PROJECT + ":<script>file.py",
                line=None,
            ),
            PROJECT,
            "issue",
        )
        self.assertNotIn("<img", finding.body)
        self.assertNotIn("<script>", finding.body)
        self.assertNotIn("@example", finding.body)
        self.assertNotIn("@example", finding.title)
        self.assertNotIn("[click](", finding.body)
        self.assertIn("\\#17", finding.body)
        self.assertEqual(finding.body.count(END_MARKER), 1)
        self.assertIn("https://sonarcloud.io/project/issues?", finding.body)
        self.assertLessEqual(len(replace(finding, message="a" * 16000).title), 240)

    def test_no_duplicates_and_manual_notes_and_labels_survive_updates(self):
        old = parse_finding(issue(), PROJECT, "issue")
        original = ticket(old, body="My introduction\n" + old.body + "\nMy own notes")
        original["labels"].append({"name": "urgent"})
        tickets = managed_tickets([original], PROJECT)
        self.assertEqual(plan_changes([old], tickets), [])
        new = replace(old, message="New description", line=30)
        changes = plan_changes([new], tickets)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual((change.action, change.number), ("update", 1))
        self.assertTrue(change.payload["body"].startswith("My introduction\n"))
        self.assertTrue(change.payload["body"].endswith("\nMy own notes"))
        self.assertIn("urgent", change.payload["labels"])
        self.assertIn("src/example\\.py:30", change.payload["body"])
        self.assertNotIn("state", change.payload)

    def test_long_remote_text_is_bounded_but_manual_notes_are_never_truncated(self):
        finding = parse_finding(issue(message="<&@" * 5000), PROJECT, "issue")
        self.assertLess(len(finding.body.encode("utf-8")), 65000)
        self.assertIn("...", finding.body)
        tickets = managed_tickets([ticket(finding, body=finding.body + "x" * 65000)], PROJECT)
        with self.assertRaisesRegex(SyncError, "shorten its notes"):
            plan_changes([finding], tickets)

    def test_explicit_fix_closes_last_and_reopening_is_idempotent(self):
        active = parse_finding(issue(), PROJECT, "issue")
        fixed = parse_finding(issue(issueStatus="FIXED"), PROJECT, "issue")
        new = parse_finding(hotspot(), PROJECT, "hotspot")
        tickets = managed_tickets([ticket(active)], PROJECT)
        changes = plan_changes([fixed, new], tickets)
        self.assertEqual([change.action for change in changes], ["create", "close"])
        self.assertEqual(changes[1].payload["state_reason"], "completed")
        closed = managed_tickets([ticket(fixed, state="closed")], PROJECT)
        self.assertEqual(plan_changes([fixed], closed), [])
        reopen = plan_changes([active], closed)[0]
        self.assertEqual(reopen.action, "reopen")
        self.assertEqual(reopen.payload["state_reason"], "reopened")
        self.assertEqual(plan_changes([], tickets), [])
        for status, resolution in (
            ("CLOSED", "REMOVED"),
            ("CLOSED", "FALSE-POSITIVE"),
            ("RESOLVED", "WONTFIX"),
            ("OPEN", "FIXED"),
        ):
            finding = parse_finding(
                issue(issueStatus=status, resolution=resolution), PROJECT, "issue"
            )
            self.assertFalse(finding.fixed)
            self.assertNotIn("state", plan_changes([finding], tickets)[0].payload)

    def test_only_owned_issues_with_valid_unique_markers_can_be_managed(self):
        finding = parse_finding(issue(), PROJECT, "issue")
        items = [
            ticket(finding, user={"id": 1, "type": "User"}),
            ticket(finding, user={"id": 41898282, "type": "User"}),
            ticket(finding, pull_request={}),
            ticket(finding, body=None),
            ticket(finding, body="Unrelated issue"),
            ticket(replace(finding, project="other")),
        ]
        self.assertEqual(managed_tickets(items, PROJECT), {})
        for body in (
            finding.body + finding.marker,
            finding.body + END_MARKER,
            finding.body.replace(END_MARKER, ""),
            END_MARKER + finding.marker,
            finding.body.replace(" issue ", " invalid "),
            17,
        ):
            with self.subTest(body=body), self.assertRaises(SyncError):
                managed_tickets([ticket(finding, body=body)], PROJECT)
        with self.assertRaisesRegex(SyncError, "Multiple GitHub issues"):
            managed_tickets([ticket(finding, 1), ticket(finding, 2)], PROJECT)
        with self.assertRaisesRegex(SyncError, "invalid state"):
            managed_tickets([ticket(finding, state="invalid")], PROJECT)
        with self.assertRaisesRegex(SyncError, "duplicate finding"):
            plan_changes([finding, finding], {})

    def test_malformed_finding_fields_abort_planning(self):
        for changes in (
            {"project": "other"},
            {"component": "other:main.py"},
            {"key": "<!-- injected -->"},
            {"line": 0},
            {"line": True},
            {"message": ""},
            {"impacts": [{}]},
            {"impacts": [None]},
            {"impacts": "HIGH"},
            {"issueStatus": 10},
        ):
            with self.subTest(changes=changes), self.assertRaises(SyncError):
                parse_finding(issue(**changes), PROJECT, "issue")
        for validator, values in (
            (object_value, [None, []]),
            (array_value, [None, {}]),
            (text_value, [None, 1, "", "a" * 16001]),
            (integer_value, [None, True, -1, 1.0]),
            (identifier, ["../bad", "a" * 257]),
        ):
            for value in values:
                with self.subTest(value=str(value)[:30]), self.assertRaises(SyncError):
                    validator(value)


class SynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ServiceFixture()
        self.sync = self.fixture.synchronizer()
        self.pause = patch("ci.sonar_sync.time.sleep")
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def test_first_sync_and_retry_are_idempotent_with_no_severity_filter(self):
        analysis, changes, labels = self.sync.prepare()
        self.assertEqual(len(changes), 2)
        self.assertEqual(labels, LABELS.keys())
        self.assertEqual(self.fixture.writes, [])
        for path, query in self.fixture.reads:
            if path.endswith("/search"):
                self.assertFalse(
                    {
                        "severities",
                        "impactSeverities",
                        "types",
                        "issueStatuses",
                        "sinceLeakPeriod",
                        "resolved",
                        "status",
                    }
                    & query.keys()
                )
        self.sync.apply(analysis, changes, labels)
        self.assertEqual(len(self.fixture.writes), 5)
        self.assertEqual(self.sync.prepare()[1:], ([], set()))

    def test_every_page_is_read_and_missing_findings_do_not_close_tickets(self):
        self.fixture.issues = [issue(f"issue-{i}") for i in range(101)]
        self.fixture.hotspots = [hotspot(f"hotspot-{i}") for i in range(100)]
        missing = parse_finding(issue("missing"), PROJECT, "issue")
        self.fixture.tickets = [ticket(missing)] + [{"pull_request": {}} for _ in range(99)]
        self.fixture.labels = [{"name": f"label-{i}"} for i in range(100)]
        _, changes, _ = self.sync.prepare()
        self.assertEqual(len(changes), 201)
        self.assertTrue(all(change.action == "create" for change in changes))
        self.assertEqual(
            [query["p"] for path, query in self.fixture.reads if path == "/api/issues/search"],
            ["1", "2"],
        )

    def test_empty_project_is_a_noop(self):
        self.fixture.issues = []
        self.fixture.hotspots = []
        _, changes, labels = self.sync.prepare()
        self.assertEqual((changes, labels), ([], set()))

    def test_stale_or_missing_analysis_never_writes(self):
        for field, value in (
            ("revision", "b" * 40),
            ("analysis_revision", "b" * 40),
            ("branches", [{"name": "other", "isMain": True}]),
        ):
            with self.subTest(field=field):
                fixture = ServiceFixture()
                setattr(fixture, field, value)
                with self.assertRaises(SyncError):
                    fixture.synchronizer().prepare()
                self.assertEqual(fixture.writes, [])
        self.sync.sonar.request.side_effect = lambda *_args, **_kwargs: {
            "branches": [{"name": "main", "isMain": True}],
            "analyses": [],
        }
        with self.assertRaisesRegex(SyncError, "No completed"):
            self.sync.prepare()

    def test_analysis_change_during_collection_or_before_apply_aborts(self):
        with patch.object(self.sync, "analysis", side_effect=["first", "second"]):
            with self.assertRaisesRegex(SyncError, "during synchronization"):
                self.sync.prepare()
        analysis, changes, labels = self.sync.prepare()
        self.fixture.analysis_key = "new-analysis"
        with self.assertRaisesRegex(SyncError, "before applying"):
            self.sync.apply(analysis, changes, labels)
        self.assertEqual(self.fixture.writes, [])

    def test_failed_or_partial_pages_never_write(self):
        for path in (
            "/api/issues/search",
            "/api/hotspots/search",
            f"/repos/{REPOSITORY}/issues",
            f"/repos/{REPOSITORY}/labels",
        ):
            self.fixture.fail_path = path
            with self.subTest(path=path), self.assertRaises(SyncError):
                self.sync.prepare()
        self.fixture.fail_path = ""
        self.fixture.issues = [issue(f"issue-{i}") for i in range(101)]
        for corruption in ("total", "pageIndex", "pageSize", "entries", "duplicate", "failure"):

            def damaged(method, path, corruption=corruption, **kwargs):
                result = self.fixture.request(method, path, **kwargs)
                if path == "/api/issues/search" and kwargs["query"]["p"] == "2":
                    if corruption == "failure":
                        raise SyncError("Rate limited")
                    if corruption == "entries":
                        result["issues"] = []
                    elif corruption == "duplicate":
                        result["issues"][0]["key"] = "issue-0"
                    else:
                        result["paging"][corruption] += 1
                return result

            self.sync.sonar.request.side_effect = damaged
            with self.subTest(corruption=corruption), self.assertRaises(SyncError):
                self.sync.prepare()
        self.assertEqual(self.fixture.writes, [])

    def test_new_main_analysis_interrupts_label_creation_ticket_creation_and_closure(self):
        for phase in ("labels", "issues", "closure"):
            fixture = ServiceFixture()
            if phase != "labels":
                fixture.existing_labels()
            if phase == "closure":
                fixture.tickets = [ticket(parse_finding(issue(), PROJECT, "issue"))]
                fixture.issues[0]["issueStatus"] = "FIXED"
            sync = fixture.synchronizer()
            analysis, changes, labels = sync.prepare()

            def changed_analysis(method, path, fixture=fixture, **kwargs):
                result = fixture.request(method, path, **kwargs)
                if method != "GET":
                    fixture.analysis_key = "new-analysis"
                return result

            sync.github.request.side_effect = changed_analysis
            with self.subTest(phase=phase), self.assertRaisesRegex(SyncError, "before applying"):
                sync.apply(analysis, changes, labels)
            self.assertEqual(len(fixture.writes), 1)
            if phase == "closure":
                self.assertEqual(fixture.tickets[0]["state"], "open")

    def test_api_limits_fail_explicitly(self):
        self.sync.sonar.request.side_effect = None
        self.sync.sonar.request.return_value = {"paging": {"total": 10001}}
        with self.assertRaisesRegex(SyncError, "10,000"):
            self.sync.sonar_pages("issue")
        self.sync.github.request.side_effect = None
        self.sync.github.request.return_value = [{}] * 101
        with self.assertRaisesRegex(SyncError, "10,000"):
            self.sync.github_pages("/issues")
        self.sync.github.request.return_value = [{}] * 100
        with self.assertRaisesRegex(SyncError, "10,000"):
            self.sync.github_pages("/issues")

    def test_partial_write_failure_is_retryable_and_closures_are_last(self):
        old = parse_finding(issue(), PROJECT, "issue")
        self.fixture.tickets = [ticket(old)]
        self.fixture.issues[0]["issueStatus"] = "FIXED"
        self.fixture.existing_labels()
        analysis, changes, labels = self.sync.prepare()
        self.fixture.fail_path = f"/repos/{REPOSITORY}/issues/1"
        with self.assertRaises(SyncError):
            self.sync.apply(analysis, changes, labels)
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertEqual(self.fixture.tickets[0]["state"], "open")
        self.fixture.fail_path = ""
        analysis, changes, labels = self.sync.prepare()
        self.assertEqual([change.action for change in changes], ["close"])
        self.sync.apply(analysis, changes, labels)
        self.assertEqual(len(self.fixture.tickets), 2)
        self.assertEqual(self.fixture.tickets[0]["state"], "closed")

    def test_edits_after_preview_are_preserved_and_require_a_new_plan(self):
        finding = parse_finding(issue(), PROJECT, "issue")
        self.fixture.tickets = [ticket(finding)]
        self.fixture.issues[0]["issueStatus"] = "FIXED"
        self.fixture.hotspots = []
        self.fixture.existing_labels()
        for changed_field, new_value in (
            ("body", finding.body + "\nA new note"),
            ("user", {"id": 1, "type": "User"}),
            ("labels", [{"name": "urgent"}]),
        ):
            self.fixture.tickets = [ticket(finding)]
            analysis, changes, labels = self.sync.prepare()
            self.fixture.tickets[0][changed_field] = new_value
            with (
                self.subTest(field=changed_field),
                self.assertRaisesRegex(SyncError, "changed after preview"),
            ):
                self.sync.apply(analysis, changes, labels)
            self.assertEqual(self.fixture.writes, [])
            self.assertEqual(self.fixture.tickets[0][changed_field], new_value)

    def test_configuration_is_validated_before_requests(self):
        for project, repository, revision in (
            ("../project", REPOSITORY, REVISION),
            (PROJECT, "https://example.invalid", REVISION),
            (PROJECT, REPOSITORY, "main"),
        ):
            with self.assertRaises(SyncError):
                Synchronizer(self.sync.sonar, self.sync.github, project, repository, revision)
        self.assertEqual(self.fixture.reads, [])


class CommandTests(unittest.TestCase):
    def test_preview_is_default_and_report_is_private_and_complete(self):
        fixture = ServiceFixture()
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "nested" / "plan.json"
            report = Path(directory) / "summary.md"
            env = {
                "SONAR_TOKEN": "sonar-test-token",
                "GH_TOKEN": "github-test-token",
                "SONAR_PROJECT_KEY": PROJECT,
                "GITHUB_REPOSITORY": REPOSITORY,
                "SONAR_REVISION": REVISION,
                "GITHUB_STEP_SUMMARY": str(report),
                "GITHUB_ACTIONS": "true",
                "GITHUB_REF": "refs/heads/main",
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["sonar-sync", "--plan", str(plan)]),
                patch("ci.sonar_api.Api.request", side_effect=fixture.request),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
            self.assertEqual(fixture.writes, [])
            data = json.loads(plan.read_text())
            self.assertEqual(data["revision"], REVISION)
            self.assertEqual(len(data["changes"]), 2)
            self.assertEqual(plan.stat().st_mode & 0o777, 0o600)
            self.assertIn("Mode: preview", report.read_text())
            self.assertNotIn("sonar-test-token", plan.read_text())
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["sonar-sync", "--plan", str(plan), "--apply"]),
                patch("ci.sonar_api.Api.request", side_effect=fixture.request),
                patch("ci.sonar_sync.time.sleep"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
            self.assertEqual(len(fixture.tickets), 2)
            self.assertIn("Mode: apply", report.read_text())

    def test_missing_credentials_and_report_errors_fail_safely(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.argv", ["sonar-sync", "--plan", "/unused"]),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(main(), 1)
        self.assertIn("token is required", errors.getvalue())
        with (
            patch.dict(
                os.environ,
                {
                    "SONAR_TOKEN": "test",
                    "GH_TOKEN": "test",
                    "SONAR_PROJECT_KEY": PROJECT,
                    "GITHUB_REPOSITORY": REPOSITORY,
                    "SONAR_REVISION": REVISION,
                },
                clear=True,
            ),
            patch("sys.argv", ["sonar-sync", "--plan", "/unused"]),
            patch("ci.sonar_sync.Synchronizer.prepare", return_value=("analysis", [], set())),
            patch("ci.sonar_sync.write_plan", side_effect=OSError("private content")),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(main(), 1)
        self.assertIn("could not write", errors.getvalue())
        self.assertNotIn("private content", errors.getvalue())

    def test_apply_requires_the_main_workflow_and_never_uses_a_local_token(self):
        for env in ({}, {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/feature"}):
            with (
                patch.dict(os.environ, env, clear=True),
                patch("sys.argv", ["sonar-sync", "--apply", "--plan", "/unused"]),
                patch("ci.sonar_api.Api.request") as request,
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(main(), 1)
            request.assert_not_called()
            self.assertIn("only supported in the main branch", errors.getvalue())

    def test_empty_plan_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            write_plan(path, "analysis", REVISION, [], set())
            self.assertEqual(json.loads(path.read_text())["changes"], [])
        self.assertIn("Create: 0", summary([], False))
