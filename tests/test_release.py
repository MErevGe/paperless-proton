import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.release import github_api, main, verified_tag, version_tag


class ReleaseTests(unittest.TestCase):
    def test_only_version_tag_pushes_select_a_release(self):
        for event, ref in (
            ("push", "refs/heads/main"),
            ("push", "refs/heads/feature/example"),
            ("push", "refs/heads/v1.2.3"),
            ("pull_request", "refs/pull/1/merge"),
            ("pull_request", "refs/tags/v1.2.3"),
            ("workflow_dispatch", "refs/tags/v1.2.3"),
            ("push", "refs/tags/checkpoint"),
        ):
            with self.subTest(event=event, ref=ref):
                self.assertEqual(version_tag(event, ref), "")
        for tag in ("v0.0.0", "v1.2.3", "v12.34.56-rc.1", "v1.2.3-alpha-1.0.abc01"):
            with self.subTest(tag=tag):
                self.assertEqual(version_tag("push", "refs/tags/" + tag), tag)

    def test_malformed_or_injectable_release_tags_are_rejected(self):
        for tag in (
            "v1",
            "v1.2",
            "v01.2.3",
            "v1.02.3",
            "v1.2.03",
            "v1.2.3-01",
            "v1.2.3-rc.01",
            "v1.2.3-",
            "v1.2.3-a..b",
            "v1.2.3+build",
            "v1.2.3/extra",
            "v1.2.3\nrelease_tag=other",
            "v1.2.3$(id)",
            "v1.2.3`id`",
            "v1.2.3-" + "a" * 128,
        ):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                version_tag("push", "refs/tags/" + tag)

    @patch("ci.release.subprocess.run")
    def test_github_lookup_is_read_only_and_propagates_errors(self, run):
        run.return_value.stdout = '{"object": {"type": "tag"}}'
        self.assertEqual(
            github_api("repos/example/project/git/ref/tags/v1.2.3"), {"object": {"type": "tag"}}
        )
        run.assert_called_once_with(
            ["gh", "api", "repos/example/project/git/ref/tags/v1.2.3"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        run.side_effect = subprocess.CalledProcessError(1, ["gh"])
        with self.assertRaises(subprocess.CalledProcessError):
            github_api("repos/example/project/git/ref/tags/v1.2.3")
        run.side_effect = None
        run.return_value.stdout = "invalid"
        with self.assertRaises(json.JSONDecodeError):
            github_api("repos/example/project/git/ref/tags/v1.2.3")

    @patch("ci.release.github_api")
    def test_signed_annotated_tag_must_target_the_tested_commit(self, api):
        tag_sha = "a" * 40
        commit = "b" * 40
        reference = {"object": {"type": "tag", "sha": tag_sha}}
        details = {
            "tag": "v1.2.3",
            "object": {"type": "commit", "sha": commit},
            "verification": {"verified": True},
        }
        api.side_effect = [reference, details]
        self.assertEqual(verified_tag("example/project", "v1.2.3", commit), tag_sha)
        self.assertEqual(api.call_args.args[0], "repos/example/project/git/tags/" + tag_sha)
        api.side_effect = [{"object": {"type": "commit", "sha": commit}}]
        with self.assertRaisesRegex(ValueError, "signed annotated"):
            verified_tag("example/project", "v1.2.3", commit)
        for verification in ({}, {"verified": False}, {"verified": "true"}):
            api.side_effect = [reference, details | {"verification": verification}]
            with self.assertRaisesRegex(ValueError, "verify the release tag signature"):
                verified_tag("example/project", "v1.2.3", commit)
        for change in (
            {"tag": "v9.9.9"},
            {"object": {"type": "tag", "sha": commit}},
            {"object": {"type": "commit", "sha": "c" * 40}},
        ):
            api.side_effect = [reference, details | change]
            with self.assertRaisesRegex(ValueError, "tested commit"):
                verified_tag("example/project", "v1.2.3", commit)

    @patch("ci.release.verified_tag", return_value="a" * 40)
    def test_workflow_outputs_only_enable_verified_tag_pushes(self, verify):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "example/project",
                "GITHUB_SHA": "b" * 40,
            }
            with patch.dict(os.environ, environment):
                main()
                self.assertEqual(output.read_text(), "release_tag=\ntag_object=\n")
                verify.assert_not_called()
                output.unlink()
                os.environ["GITHUB_REF"] = "refs/tags/v1.2.3"
                main()
                self.assertEqual(
                    output.read_text(), "release_tag=v1.2.3\ntag_object=" + "a" * 40 + "\n"
                )
                verify.assert_called_once_with("example/project", "v1.2.3", "b" * 40)
                output.unlink()
                verify.side_effect = ValueError("Unverified signature")
                with self.assertRaises(ValueError):
                    main()
                self.assertFalse(output.exists())
