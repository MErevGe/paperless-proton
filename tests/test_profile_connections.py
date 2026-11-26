import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bridge_container.import_profiles import ImportProfiles
from bridge_container.paperless import Paperless
from bridge_container.settings import Settings


class ProfileConnectionTests(unittest.TestCase):
    def test_two_profiles_send_their_own_tokens_and_update_only_their_owned_resources(self):
        tokens = {"first-profile-api-token": 3, "second-profile-api-token": 5}
        resources = {"mail_accounts": {}, "mail_rules": {}}
        received = []
        secrets = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle_request(self):
                token = self.headers["Authorization"].removeprefix("Token ")
                owner = tokens[token]
                endpoint = self.path.removeprefix("/api/")
                size = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(size)) if size else None
                received.append((owner, self.command, endpoint, payload))
                if endpoint == "ui_settings/":
                    response = {"user": {"id": owner, "username": f"user-{owner}"}}
                elif endpoint == "mail_accounts/?page_size=1":
                    response = {"results": []}
                elif endpoint == "mail_accounts/test/":
                    response = {"success": payload["username"] == f"user-{owner}@example.test"}
                else:
                    resource, *identifier = endpoint.rstrip("/").split("/")
                    items = resources[resource]
                    if self.command == "POST":
                        key = len(items) + 1
                        items[key] = payload | {"id": key}
                    else:
                        key = int(identifier[0])
                        if items[key]["owner"] != owner:
                            self.send_error(403)
                            return
                        if self.command == "PATCH":
                            items[key].update(payload)
                    response = items[key]
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = handle_request
            do_POST = handle_request
            do_PATCH = handle_request

        def password_store(args, **kwargs):
            if args[1] == "insert":
                secrets[args[-1]] = kwargs["input"]
                return SimpleNamespace(stdout="")
            return SimpleNamespace(stdout=secrets[args[-1]])

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("bridge_container.paperless.target_address", return_value="127.0.0.1"),
            patch("bridge_container.paperless.subprocess.run", side_effect=password_store),
        ):
            settings = Settings(Path(directory))
            settings.save({"host": "bridge.example.test"})
            profiles = ImportProfiles(settings)
            clients = []
            for index, (token, user) in enumerate(tokens.items(), 1):
                profile = profiles.get(f"proton-{index}")
                bridge = MagicMock()
                bridge.credentials.return_value = {
                    "username": f"user-{user}@example.test",
                    "password": f"bridge-password-{index}",
                }
                client = Paperless(profile, bridge)
                client.save(
                    profile.value
                    | {
                        "paperless_url": f"http://paperless.test:{server.server_port}",
                        "allow_http": True,
                        "token": token,
                        "address": f"user-{user}@example.test",
                        "folder": f"Folders/Source-{index}",
                        "destination": f"Folders/Processed-{index}",
                    }
                )
                client.apply()
                clients.append(client)
                bridge.credentials.assert_called_with(
                    f"proton-{index}", f"user-{user}@example.test"
                )
            clients[0].settings.save({"folder": "Folders/Changed"})
            clients[0].apply()
            self.assertEqual(len(secrets), 2)
            self.assertEqual(len(resources["mail_accounts"]), 2)
            self.assertEqual(len(resources["mail_rules"]), 2)
            self.assertEqual(resources["mail_rules"][1]["folder"], "Folders/Changed")
            self.assertEqual(resources["mail_rules"][2]["folder"], "Folders/Source-2")
            for index, owner in ((1, 3), (2, 5)):
                rule = resources["mail_rules"][index]
                self.assertEqual(rule["account"], index)
                self.assertEqual(rule["owner"], owner)
                self.assertTrue(rule["assign_owner_from_rule"])
                self.assertEqual(rule["action_parameter"], f"Folders/Processed-{index}")
                self.assertEqual(resources["mail_accounts"][index]["owner"], owner)
            for owner, method, _endpoint, payload in received:
                if method == "PATCH":
                    self.assertEqual(owner, 3)
                    self.assertEqual(payload["owner"], 3)
