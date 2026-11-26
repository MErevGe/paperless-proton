import hashlib
import os
from pathlib import Path
from typing import Any

from bridge_container.settings import Settings, save_json, text_field

PROFILE_DEFAULTS: dict[str, Any] = {
    "paperless_url": "",
    "paperless_ca": "",
    "allow_http": False,
    "account_name": "Proton Mail",
    "rule_name": "Proton attachments",
    "folder": "Folders/Paperless",
    "destination": "Folders/Processed",
    "attachment_filter": "*.pdf",
    "maximum_age": 30,
    "address": "",
    "account_id": None,
    "rule_id": None,
    "paperless_user": None,
    "linked_user_id": None,
    "applied": False,
}


class ImportProfile:
    def __init__(self, settings: Settings, proton_id: Any):
        self.settings = settings
        self.proton_id = text_field(proton_id, "Proton account")
        self.data = settings.data
        digest = hashlib.sha256(self.proton_id.encode()).hexdigest()
        self.token_key = f"paperless-proton/profiles/{digest}/api-token"

    @property
    def token_path(self) -> Path:
        store = Path(os.environ.get("PASSWORD_STORE_DIR", self.data / "password-store"))
        return store / (self.token_key + ".gpg")

    @property
    def value(self) -> dict[str, Any]:
        stored: dict[str, Any] = self.settings.value["profiles"].get(self.proton_id, {})
        return (
            PROFILE_DEFAULTS
            | stored
            | {
                "proton_id": self.proton_id,
                "host": self.settings.value["host"],
                "port": self.settings.value["port"],
            }
        )

    def save(self, changes: dict[str, Any]) -> None:
        values = self.value | changes
        profile = {key: values[key] for key in PROFILE_DEFAULTS}
        self.settings.save(
            {"profiles": self.settings.value["profiles"] | {self.proton_id: profile}}
        )


class ImportProfiles:
    def __init__(self, settings: Settings):
        self.settings = settings
        if "paperless_url" in settings.value:
            self.migrate()
        profiles = settings.value["profiles"]
        if not isinstance(profiles, dict) or any(
            not isinstance(value, dict) for value in profiles.values()
        ):
            raise ValueError("Invalid import profiles")

    def get(self, proton_id: Any) -> ImportProfile:
        return ImportProfile(self.settings, proton_id)

    def public(self) -> dict[str, Any]:
        return {
            proton_id: {
                **{key: profile.value[key] for key in PROFILE_DEFAULTS},
                "token_saved": profile.token_path.is_file(),
            }
            for proton_id in self.settings.value["profiles"]
            for profile in [self.get(proton_id)]
        }

    def migrate(self) -> None:
        old = self.settings.value
        profiles = dict(old["profiles"])
        proton_id = old.get("proton_id")
        backup = self.settings.path.with_name("settings.before-profiles.json")
        if not backup.exists():
            save_json(backup, old)
        if proton_id:
            profile = self.get(proton_id)
            if proton_id not in profiles:
                values = PROFILE_DEFAULTS | old
                profiles[proton_id] = {key: values[key] for key in PROFILE_DEFAULTS}
                store = Path(
                    os.environ.get("PASSWORD_STORE_DIR", self.settings.data / "password-store")
                )
                legacy_token = store / "paperless-proton/api-token.gpg"
                if legacy_token.is_file():
                    profile.token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    temporary = profile.token_path.with_suffix(".tmp")
                    temporary.write_bytes(legacy_token.read_bytes())
                    temporary.chmod(0o600)
                    temporary.replace(profile.token_path)
        self.settings.replace({"host": old["host"], "port": old["port"], "profiles": profiles})
