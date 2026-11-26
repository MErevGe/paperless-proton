import ssl
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from imapclient import IMAPClient

from bridge_container.mail_folders import list_folders


class MailFolderTests(unittest.TestCase):
    def setUp(self):
        self.bridge = MagicMock()
        self.bridge.credentials.return_value = {
            "username": "mail@example.test",
            "password": "secret",
        }

    @patch("bridge_container.mail_folders.ssl.create_default_context")
    @patch("bridge_container.mail_folders.IMAPClient")
    def test_lists_selectable_folders_with_verified_local_connection(self, client_type, context):
        client = MagicMock()
        client_type.return_value.__enter__.return_value = client
        client.list_folders.return_value = [
            ((b"\\NoSelect",), b"/", "Folders"),
            ((), b"/", "Folders/Überweisungen"),
            ((b"\\HasNoChildren",), b"/", 'Folders/Tax & "Receipts"'),
            ((), b"/", "INBOX"),
            ((), b"/", "INBOX"),
        ]
        self.assertEqual(
            list_folders(Path("/data"), self.bridge, "one", "mail@example.test"),
            ["INBOX", 'Folders/Tax & "Receipts"', "Folders/Überweisungen"],
        )
        self.bridge.credentials.assert_called_once_with("one", "mail@example.test")
        client_type.assert_called_once_with(
            "localhost", port=1993, ssl_context=context.return_value, timeout=15
        )
        context.assert_called_once_with(cafile="/data/tls/ca.crt")
        client.login.assert_called_once_with("mail@example.test", "secret")
        client.select_folder.assert_not_called()
        self.assertTrue(client_type.return_value.__exit__.called)
        client.list_folders.return_value = []
        self.assertEqual(list_folders(Path("/data"), self.bridge, "one", "mail@example.test"), [])
        for error in (
            IMAPClient.Error("secret"),
            OSError("secret"),
            ssl.SSLError("secret"),
            ValueError("secret"),
        ):
            client.list_folders.side_effect = error
            with self.assertRaisesRegex(ValueError, "Could not load Proton folders") as caught:
                list_folders(Path("/data"), self.bridge, "one", "mail@example.test")
            self.assertNotIn("secret", str(caught.exception))

    @patch("bridge_container.mail_folders.IMAPClient")
    def test_invalid_account_never_connects(self, client):
        self.bridge.credentials.side_effect = ValueError("Choose a connected account")
        with self.assertRaises(ValueError):
            list_folders(Path("/data"), self.bridge, "unknown", "mail@example.test")
        client.assert_not_called()
