import unittest
from unittest.mock import patch

from sonar_fixtures import PROJECT, REPOSITORY, ServiceFixture, hotspot, issue, ticket

from ci.sonar_api import SyncError
from ci.sonar_findings import managed_tickets, parse_finding, plan_changes


class FalsePositivePlanningTests(unittest.TestCase):
    def test_modern_and_legacy_false_positives_close_with_a_distinct_reason(self):
        active = parse_finding(issue(), PROJECT, "issue")
        original = ticket(active, body="Introduction\n" + active.body + "\nKeep these notes")
        original["labels"].append({"name": "urgent"})
        tickets = managed_tickets([original], PROJECT)
        for status in ("FALSE_POSITIVE", "RESOLVED", "CLOSED"):
            data = issue(issueStatus=status, resolution="FALSE-POSITIVE")
            if status != "FALSE_POSITIVE":
                data["status"] = data.pop("issueStatus")
            finding = parse_finding(data, PROJECT, "issue")
            with self.subTest(status=status):
                change = plan_changes([finding], tickets)[0]
                self.assertEqual((change.action, change.number), ("close", 1))
                self.assertEqual(change.payload["state"], "closed")
                self.assertEqual(change.payload["state_reason"], "not_planned")
                self.assertIn("false-positive", change.payload["labels"])
                self.assertIn("urgent", change.payload["labels"])
                self.assertTrue(change.payload["body"].startswith("Introduction\n"))
                self.assertTrue(change.payload["body"].endswith("\nKeep these notes"))
                self.assertFalse(finding.fixed)

    def test_active_accepted_deferred_and_safe_hotspots_are_not_false_positives(self):
        findings = [
            parse_finding(issue(issueStatus=status, resolution="FALSE-POSITIVE"), PROJECT, "issue")
            for status in ("OPEN", "CONFIRMED", "REOPENED", "ACCEPTED", "DEFERRED")
        ]
        findings += [
            parse_finding(hotspot(status="REVIEWED", resolution=resolution), PROJECT, "hotspot")
            for resolution in ("SAFE", "ACKNOWLEDGED", "FALSE-POSITIVE")
        ]
        for finding in findings:
            with self.subTest(kind=finding.kind, status=finding.status):
                change = plan_changes([finding], {})[0]
                self.assertNotIn("state", change.payload)
                self.assertNotIn("false-positive", change.payload["labels"])
                self.assertIsNone(finding.close_reason)

    def test_already_closed_issues_get_the_correct_reason_and_label(self):
        for status, previous_reason, expected_reason in (
            ("FALSE_POSITIVE", "completed", "not_planned"),
            ("FALSE_POSITIVE", None, "not_planned"),
            ("FIXED", "not_planned", "completed"),
        ):
            finding = parse_finding(issue(issueStatus=status), PROJECT, "issue")
            original = ticket(finding, state="closed", state_reason=previous_reason)
            original["labels"].append({"name": "false-positive"})
            with self.subTest(status=status, previous_reason=previous_reason):
                change = plan_changes([finding], managed_tickets([original], PROJECT))[0]
                self.assertEqual(change.action, "close")
                self.assertEqual(change.payload["state_reason"], expected_reason)
                self.assertEqual("false-positive" in change.payload["labels"], status != "FIXED")

    def test_invalid_state_reasons_are_rejected(self):
        finding = parse_finding(issue(), PROJECT, "issue")
        for reason in (True, [], {}):
            with self.subTest(reason=reason), self.assertRaises(SyncError):
                managed_tickets([ticket(finding, state_reason=reason)], PROJECT)


class FalsePositiveSynchronizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ServiceFixture()
        self.fixture.hotspots = []
        self.fixture.existing_labels()
        self.sync = self.fixture.synchronizer()
        pause = patch("ci.sonar_sync.time.sleep")
        pause.start()
        self.addCleanup(pause.stop)

    def test_classification_reopening_and_reclassification_reuse_the_same_issue(self):
        self.sync.apply(*self.sync.prepare())
        original = self.fixture.tickets[0]
        number = original["number"]
        original["body"] += "\nRetain manual notes"
        original["labels"].append({"name": "urgent"})
        for status, expected_state, expected_reason in (
            ("FALSE_POSITIVE", "closed", "not_planned"),
            ("OPEN", "open", "reopened"),
            ("FALSE_POSITIVE", "closed", "not_planned"),
            ("ACCEPTED", "open", "reopened"),
            ("FALSE_POSITIVE", "closed", "not_planned"),
            ("DEFERRED", "open", "reopened"),
            ("FIXED", "closed", "completed"),
        ):
            self.fixture.issues[0]["issueStatus"] = status
            with self.subTest(status=status):
                self.sync.apply(*self.sync.prepare())
                self.assertEqual(len(self.fixture.tickets), 1)
                self.assertEqual(original["number"], number)
                self.assertEqual(original["state"], expected_state)
                self.assertEqual(original["state_reason"], expected_reason)
                labels = {label["name"] for label in original["labels"]}
                self.assertEqual("false-positive" in labels, status == "FALSE_POSITIVE")
                self.assertIn("urgent", labels)
                self.assertTrue(original["body"].endswith("\nRetain manual notes"))
                self.assertEqual(self.sync.prepare()[1:], ([], set()))

    def test_first_seen_false_positive_is_created_then_closed_after_other_changes(self):
        self.fixture.issues = [issue("first", issueStatus="FALSE_POSITIVE"), issue("second")]
        self.sync.apply(*self.sync.prepare())
        self.assertEqual(
            [method for method, _, _ in self.fixture.writes], ["POST", "POST", "PATCH"]
        )
        closed, active = self.fixture.tickets
        self.assertEqual((closed["state"], closed["state_reason"]), ("closed", "not_planned"))
        self.assertEqual(active["state"], "open")
        self.assertEqual(self.sync.prepare()[1:], ([], set()))

    def test_failed_closure_after_creation_is_retryable_without_duplicates(self):
        self.fixture.issues[0]["issueStatus"] = "FALSE_POSITIVE"
        self.fixture.fail_path = f"/repos/{REPOSITORY}/issues/1"
        with self.assertRaises(SyncError):
            self.sync.apply(*self.sync.prepare())
        self.assertEqual(len(self.fixture.tickets), 1)
        self.assertEqual(self.fixture.tickets[0]["state"], "open")
        self.fixture.fail_path = ""
        self.sync.apply(*self.sync.prepare())
        self.assertEqual(len(self.fixture.tickets), 1)
        self.assertEqual(self.fixture.tickets[0]["state_reason"], "not_planned")
        self.assertEqual(self.sync.prepare()[1:], ([], set()))

    def test_new_analysis_or_manual_edit_after_creation_prevents_closure(self):
        for mutation in ("analysis", "body"):
            fixture = ServiceFixture()
            fixture.hotspots = []
            fixture.existing_labels()
            fixture.issues[0]["issueStatus"] = "FALSE_POSITIVE"
            sync = fixture.synchronizer()

            def changed(method, path, fixture=fixture, mutation=mutation, **kwargs):
                result = fixture.request(method, path, **kwargs)
                if method == "POST":
                    if mutation == "analysis":
                        fixture.analysis_key = "new-analysis"
                    else:
                        fixture.tickets[0]["body"] += "\nNew manual note"
                return result

            sync.github.request.side_effect = changed
            with self.subTest(mutation=mutation), self.assertRaises(SyncError):
                sync.apply(*sync.prepare())
            self.assertEqual(len(fixture.writes), 1)
            self.assertEqual(fixture.tickets[0]["state"], "open")

    def test_unverified_creation_response_never_closes_another_issue(self):
        self.fixture.issues[0]["issueStatus"] = "FALSE_POSITIVE"

        def unowned(method, path, **kwargs):
            result = self.fixture.request(method, path, **kwargs)
            if method == "POST":
                result["user"] = {"id": 1, "type": "User"}
            return result

        self.sync.github.request.side_effect = unowned
        with self.assertRaisesRegex(SyncError, "could not be verified"):
            self.sync.apply(*self.sync.prepare())
        self.assertEqual(len(self.fixture.writes), 1)
        self.assertEqual(self.fixture.tickets[0]["state"], "open")
