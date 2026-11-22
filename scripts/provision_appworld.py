"""Provision the pinned AppWorld runtime and data for agent-validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

from agent_validation.config import load_profile

REPOSITORY = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPOSITORY / "config" / "appworld" / "requirements.txt"
DATA_URL = "https://s3.us-west-2.amazonaws.com/appworld.dev/data-{version}.bundle"
UNPACK_DATA = """
import sys
from appworld.common.constants import PASSWORD, SALT
from appworld.common.utils import unpack_bundle
unpack_bundle(
    bundle_file_path=sys.argv[1],
    base_directory=sys.argv[2],
    password=PASSWORD,
    salt=SALT,
)
"""
VALIDATE_TASKS = """
import json
from appworld import load_task_ids
print(json.dumps({name: load_task_ids(name) for name in ('train', 'dev', 'test_normal')}))
"""


def run(command: list[str], *, environment: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed.stdout


def check_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch for {path}: expected {expected}, found {actual}")


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    profile = load_profile("appworld", REPOSITORY / "config" / "profiles").profile
    settings = profile.appworld
    assert settings is not None
    runtime = arguments.runtime.resolve()
    root = arguments.root.resolve()
    if runtime.exists():
        parser.error(f"Runtime already exists: {runtime}")
    if (root / "data").exists():
        parser.error(f"AppWorld data already exists: {root / 'data'}")
    downloads = root / ".provision"
    downloads.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment.update(
        {
            "APPWORLD_ROOT": str(root),
            "APPWORLD_CACHE": str(root / ".appworld-cache"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    try:
        run([sys.executable, "-m", "venv", str(runtime)])
        python = runtime / "bin" / "python"
        run(
            [
                str(python),
                "-m",
                "pip",
                "download",
                "--no-deps",
                "--only-binary=:all:",
                "--dest",
                str(downloads),
                f"appworld=={settings.package_version}",
            ],
            environment=environment,
        )
        wheels = list(downloads.glob("appworld-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one AppWorld wheel, found {len(wheels)}")
        check_sha256(wheels[0], settings.wheel_sha256)
        dependencies = downloads / "dependencies.txt"
        dependencies.write_text(
            "".join(
                line
                for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines(keepends=True)
                if not line.lower().startswith("appworld==")
            ),
            encoding="utf-8",
        )
        run(
            [str(python), "-m", "pip", "install", "--requirement", str(dependencies)],
            environment=environment,
        )
        run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])],
            environment=environment,
        )
        version = run(
            [str(python), "-c", "import appworld; print(appworld.__version__)"],
            environment=environment,
        ).strip()
        if version != settings.package_version:
            raise RuntimeError(f"Installed AppWorld version {version} does not match the profile")
        run([str(python), "-m", "appworld.cli", "install"], environment=environment)
        bundle = downloads / "data.bundle"
        download(DATA_URL.format(version=settings.data_version), bundle)
        check_sha256(bundle, settings.data_sha256)
        (root / "data").mkdir()
        run(
            [str(python), "-c", UNPACK_DATA, str(bundle), str(root)],
            environment=environment,
        )
        actual = json.loads(run([str(python), "-c", VALIDATE_TASKS], environment=environment))
        expected = {
            split: [task.id for task in profile.tasks if task.split == split]
            for split in ("train", "dev", "test_normal")
        }
        if actual != expected:
            raise RuntimeError("Provisioned AppWorld task IDs do not match the registered profile")
        counts = Counter(task.split for task in profile.tasks)
        print(f"Provisioned AppWorld {version} with task counts {dict(counts)}")
        print(f"AGENT_VALIDATION_APPWORLD_PYTHON={python}")
        print(f"AGENT_VALIDATION_APPWORLD_ROOT={root}")
        return 0
    finally:
        shutil.rmtree(downloads, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
