import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

VERSION_PATTERN = re.compile(
    r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


def version_tag(event: str, ref: str) -> str:
    if event != "push" or not ref.startswith("refs/tags/v"):
        return ""
    tag = ref.removeprefix("refs/tags/")
    match = VERSION_PATTERN.fullmatch(tag)
    if not match or len(tag) > 128:
        raise ValueError("Use a version tag such as v1.2.3 or v1.2.3-rc.1")
    prerelease = match[4] or ""
    if any(
        part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")
    ):
        raise ValueError("Numeric prerelease identifiers must not have leading zeroes")
    return tag


def github_api(endpoint: str) -> Any:
    result = subprocess.run(
        ["gh", "api", endpoint], check=True, capture_output=True, text=True, timeout=30
    )
    return json.loads(result.stdout)


def verified_tag(repository: str, tag: str, commit: str) -> str:
    reference = github_api(f"repos/{repository}/git/ref/tags/{tag}")["object"]
    if reference["type"] != "tag":
        raise ValueError("Release tags must be signed annotated tags")
    tag_object = str(reference["sha"])
    details = github_api(f"repos/{repository}/git/tags/{tag_object}")
    if details.get("verification", {}).get("verified") is not True:
        raise ValueError("GitHub must verify the release tag signature before publishing")
    if (
        details.get("tag") != tag
        or details["object"]["type"] != "commit"
        or details["object"]["sha"] != commit
    ):
        raise ValueError("The release tag must point directly to the tested commit")
    return tag_object


def main() -> None:
    tag = version_tag(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"])
    tag_object = ""
    if tag:
        tag_object = verified_tag(os.environ["GITHUB_REPOSITORY"], tag, os.environ["GITHUB_SHA"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"release_tag={tag}\ntag_object={tag_object}\n")


if __name__ == "__main__":
    main()
