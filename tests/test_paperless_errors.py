import json
import unittest

from bridge_container.paperless_errors import PaperlessError


class PaperlessErrorTests(unittest.TestCase):
    def test_validation_errors_identify_fields_without_exposing_upstream_values(self):
        for endpoint, body, expected in (
            ("mail_rules/1/", {"maximum_age": ["private-value"]}, "0 to 32767"),
            (
                "mail_rules/",
                {"folder": ["private-value"], "action_parameter": {"private-value": []}},
                "source folder, destination folder",
            ),
            (
                "mail_accounts/1/",
                {"password": ["private-value"], "username": "private-value"},
                "Bridge username, Bridge password",
            ),
            ("mail_accounts/test/", {"imap_server": ["private-value"]}, "Bridge hostname"),
        ):
            with self.subTest(endpoint=endpoint):
                message = str(PaperlessError(400, endpoint, json.dumps(body).encode()))
                self.assertIn(expected, message)
                self.assertIn("HTTP 400", message)
                self.assertNotIn("private-value", message)
                self.assertNotIn("certificate", message)

    def test_unstructured_errors_use_operation_specific_safe_messages(self):
        for endpoint, expected in (
            ("mail_rules/1/", "import rule"),
            ("mail_accounts/test/", "Bridge connection test"),
            ("mail_accounts/", "email account settings"),
            ("unknown/", "rejected the request"),
        ):
            for body in (
                b"private-value",
                b"<html>private-value</html>",
                b'"private-value"',
                b'["private-value"]',
                b'{"private-value":["private-value"]}',
                b"null",
                b"\xff",
            ):
                with self.subTest(endpoint=endpoint, body=body):
                    message = str(PaperlessError(400, endpoint, body))
                    self.assertIn(expected, message)
                    self.assertNotIn("private-value", message)
