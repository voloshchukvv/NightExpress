#!/usr/bin/env python3
"""Download the pinned Monaco Editor build into src/nightexpress/resources/editor/monaco/.

The archive is verified against the npm registry integrity hash pinned below, then only the
AMD `min/vs` build is kept and the language services we never use (TypeScript, CSS, HTML and
their workers) are removed. Run it once after cloning:  .venv/bin/python scripts/fetch_monaco.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VERSION = "0.57.0"
INTEGRITY = (
    "sha512-"  # from https://registry.npmjs.org/monaco-editor/0.57.0 -> dist.integrity
    "5BkI9KGoqrNvBGUe15/QlZq3OooZ8WLg1AxTpaqHRCP3HNpzPPZKE2EDz8M7c+VR"
    "mCeUw1Brp4cx/PWm3kI/5A=="
)
URL = f"https://registry.npmjs.org/monaco-editor/-/monaco-editor-{VERSION}.tgz"

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src" / "nightexpress" / "resources" / "editor" / "monaco"

# Paths under min/vs that are not needed for a Rust/TOML editor.
PRUNE_DIRS = ("language/typescript", "language/css", "language/html", "nls/lang")
PRUNE_PREFIXES = ("ts.worker", "css.worker", "html.worker")


def verify(data: bytes) -> None:
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != INTEGRITY:
        sys.exit(f"integrity mismatch for {URL}\n  expected {INTEGRITY}\n  got      {digest}")


def extract(data: bytes, dest: Path) -> None:
    prefix = "package/min/vs/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = Path(member.name[len(prefix) :])
            if relative.is_absolute() or ".." in relative.parts:
                sys.exit(f"unsafe path in archive: {member.name}")
            target = dest / "vs" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            assert source is not None
            target.write_bytes(source.read())
        for name in ("package/LICENSE", "package/ThirdPartyNotices.txt"):
            source = archive.extractfile(name)
            assert source is not None
            (dest / Path(name).name).write_bytes(source.read())


def prune(vs: Path) -> None:
    for directory in PRUNE_DIRS:
        shutil.rmtree(vs / directory, ignore_errors=True)
    # Only the bundled worker copies in assets/ are safe to drop: the top-level *.worker-*.js
    # chunks are static dependencies of editor.main and must stay.
    for path in list((vs / "assets").glob("*.js")):
        if path.name.startswith(PRUNE_PREFIXES):
            path.unlink()


def main() -> None:
    version_file = DEST / "VERSION"
    if version_file.is_file() and version_file.read_text().strip() == VERSION:
        print(f"Monaco {VERSION} is already present in {DEST}")
        return
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read()
    verify(data)
    staging = DEST.with_name("monaco.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    extract(data, staging)
    prune(staging / "vs")
    (staging / "VERSION").write_text(VERSION + "\n")
    shutil.rmtree(DEST, ignore_errors=True)
    staging.rename(DEST)
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"Monaco {VERSION} installed in {DEST} ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
