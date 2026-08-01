import argparse
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from getpass import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_FILE = ROOT / "main.py"
PYPROJECT_FILE = ROOT / "pyproject.toml"
DIST_DIR = ROOT / "dist"


def read_version() -> str:
    text = MAIN_FILE.read_text(encoding="utf-8")
    match = re.search(r'APPLICATION_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', text)
    if not match:
        raise RuntimeError("Could not find APPLICATION_VERSION in main.py")
    return match.group(1)


def update_pyproject_version(version: str) -> None:
    text = PYPROJECT_FILE.read_text(encoding="utf-8")
    marker = 'version = '
    start = text.find(marker)
    if start == -1:
        raise RuntimeError("Could not update pyproject.toml version")

    value_start = text.find('"', start)
    value_end = text.find('"', value_start + 1)
    if value_start == -1 or value_end == -1:
        raise RuntimeError("Could not update pyproject.toml version")

    new_text = text[:value_start + 1] + version + text[value_end:]
    PYPROJECT_FILE.write_text(new_text, encoding="utf-8")


def run(command: list[str], cwd: Path | None = None) -> None:
    print(f"> {' '.join(command)}")
    completed = subprocess.run(command, cwd=str(cwd or ROOT), check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def resolve_pypi_token(cli_token: str | None) -> str:
    if cli_token:
        return cli_token
    if os.environ.get("PYPI_TOKEN"):
        return os.environ["PYPI_TOKEN"]
    try:
        return getpass("PyPI token: ")
    except KeyboardInterrupt:
        raise SystemExit(1)


def is_version_on_pypi(package_name: str, version: str) -> bool:
    url = f"https://pypi.org/pypi/{package_name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and publish the prompt-copilot-cli package")
    parser.add_argument("--pypi-token", dest="pypi_token", default=None, help="PyPI token for upload")
    parser.add_argument("--skip-upload", action="store_true", help="Build and validate without uploading")
    args = parser.parse_args()

    version = read_version()
    print(f"Detected version: {version}")

    update_pyproject_version(version)
    print(f"Updated pyproject.toml to version {version}")

    if DIST_DIR.exists():
        for item in DIST_DIR.iterdir():
            if item.is_file() or item.is_symlink():
                item.unlink()
            else:
                for child in sorted(item.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                item.rmdir()

    run([sys.executable, "-m", "build"])
    artifacts = sorted(str(p) for p in DIST_DIR.glob("prompt_copilot_cli-*"))
    if not artifacts:
        raise RuntimeError("No build artifacts found in dist/")
    run([sys.executable, "-m", "twine", "check", *artifacts])

    if args.skip_upload:
        print("Skipping upload because --skip-upload was requested.")
        return

    if is_version_on_pypi("prompt-copilot-cli", version):
        print(f"Version {version} is already published on PyPI. Skipping upload.")
        return

    pypi_token = resolve_pypi_token(args.pypi_token)
    print("Release ready. Uploading to PyPI...")
    run([
        sys.executable,
        "-m",
        "twine",
        "upload",
        *artifacts,
        "--username",
        "__token__",
        "--password",
        pypi_token,
    ])


if __name__ == "__main__":
    main()
