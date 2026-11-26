import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_container.import_profiles import PROFILE_DEFAULTS, ImportProfiles
from bridge_container.settings import Settings


class ImportProfilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        environment = patch.dict(os.environ, {"PASSWORD_STORE_DIR": str(self.data / "secrets")})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = Settings(self.data)

    def legacy(self, identifier="one"):
        values = PROFILE_DEFAULTS | {
            "proton_id": identifier,
            "paperless_url": "https://archive.example.test",
            "address": "mail@example.test",
            "folder": "Folders/Private",
            "account_id": 7,
            "rule_id": 9,
        }
        self.settings.save(values)
        token = self.data / "secrets/paperless-proton/api-token.gpg"
        token.parent.mkdir(parents=True)
        token.write_bytes(b"encrypted legacy token")
        return token

    def test_profiles_persist_independently_with_safe_secret_paths(self):
        profiles = ImportProfiles(self.settings)
        first, second = profiles.get("one"), profiles.get("../../two")
        first.save({"folder": "Private", "account_id": 7, "rule_id": 8})
        second.save(
            {"folder": "Work", "account_id": 9, "rule_id": 10, "token": "hidden", "host": "wrong"}
        )
        self.assertNotEqual(first.token_key, second.token_key)
        self.assertNotIn("..", second.token_key)
        self.assertTrue(second.token_path.is_relative_to(self.data / "secrets"))
        second.token_path.parent.mkdir(parents=True)
        second.token_path.write_bytes(b"encrypted second token")
        loaded = ImportProfiles(Settings(self.data))
        self.assertEqual(loaded.get("one").value["folder"], "Private")
        self.assertEqual(loaded.get("../../two").value["rule_id"], 10)
        self.assertEqual(second.value["host"], self.settings.value["host"])
        self.settings.save({"host": "bridge.example.test"})
        self.assertEqual(first.value["host"], "bridge.example.test")
        self.assertEqual(second.value["host"], "bridge.example.test")
        public = profiles.public()
        self.assertFalse(public["one"]["token_saved"])
        self.assertTrue(public["../../two"]["token_saved"])
        self.assertNotIn("hidden", self.settings.path.read_text())
        self.assertNotIn("encrypted", json.dumps(public))
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        for identifier in (None, "", "x" * 257, {}, "bad\nvalue"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                profiles.get(identifier)

    def test_migration_preserves_settings_links_and_encrypted_token_once(self):
        legacy_token = self.legacy()
        before = self.settings.path.read_bytes()
        profiles = ImportProfiles(self.settings)
        profile = profiles.get("one")
        self.assertEqual(profile.token_path.read_bytes(), legacy_token.read_bytes())
        self.assertEqual(profile.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(profile.value["folder"], "Folders/Private")
        self.assertEqual(profile.value["account_id"], 7)
        self.assertEqual(profile.value["rule_id"], 9)
        self.assertIsNone(profile.value["linked_user_id"])
        backup = self.settings.path.with_name("settings.before-profiles.json")
        self.assertEqual(backup.read_bytes(), before)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(self.settings.value), {"host", "port", "profiles"})
        profile.save({"folder": "Changed"})
        profile.token_path.write_bytes(b"rotated token")
        ImportProfiles(Settings(self.data))
        self.assertEqual(profile.value["folder"], "Changed")
        self.assertEqual(profile.token_path.read_bytes(), b"rotated token")
        self.assertEqual(backup.read_bytes(), before)

    def test_failed_migration_leaves_original_settings_and_can_retry(self):
        self.legacy()
        before = self.settings.path.read_bytes()
        with patch.object(self.settings, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ImportProfiles(self.settings)
        self.assertEqual(self.settings.path.read_bytes(), before)
        profiles = ImportProfiles(self.settings)
        self.assertTrue(profiles.public()["one"]["token_saved"])
        self.assertEqual(
            self.settings.path.with_name("settings.before-profiles.json").read_bytes(), before
        )

    def test_migration_handles_unconfigured_or_missing_token_and_existing_profile(self):
        token = self.legacy(identifier="")
        profiles = ImportProfiles(self.settings)
        self.assertEqual(profiles.public(), {})
        self.settings.save(PROFILE_DEFAULTS | {"proton_id": "one"})
        token.unlink()
        profiles = ImportProfiles(self.settings)
        self.assertFalse(profiles.public()["one"]["token_saved"])
        profile = profiles.get("one")
        profile.save({"folder": "Existing"})
        self.settings.save(PROFILE_DEFAULTS | {"proton_id": "one", "folder": "Legacy"})
        ImportProfiles(self.settings)
        self.assertEqual(profile.value["folder"], "Existing")

    def test_invalid_stored_profiles_fail_closed(self):
        for value in (None, [], {"one": "bad"}):
            self.settings.save({"profiles": value})
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "Invalid import profiles"),
            ):
                ImportProfiles(self.settings)
