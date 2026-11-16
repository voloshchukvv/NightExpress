#!/usr/bin/env python3
"""Download the pinned xterm.js build into src/nightexpress/resources/terminal/xterm/.

xterm.js is the terminal of VS Code (MIT). Each npm archive is verified against the registry
integrity hash pinned below; only the UMD bundles, the stylesheet and the licenses are kept.
Run it once after cloning:  .venv/bin/python scripts/fetch_xterm.py
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

# (npm package, version, dist.integrity from https://registry.npmjs.org/<package>/<version>,
#  {archive path: installed name})
PACKAGES = (
    (
        "@xterm/xterm",
        "6.0.0",
        "sha512-TQwDdQGtwwDt+2cgKDLn0IRaSxYu1tSUjgKarSDkUM0ZNiSRXFpjxEsvc/Zgc5kq5omJ+V0a8/kIM2WD3sMOYg==",
        {
            "package/lib/xterm.js": "xterm.js",
            "package/css/xterm.css": "xterm.css",
            "package/LICENSE": "LICENSE-xterm",
        },
    ),
    (
        "@xterm/addon-fit",
        "0.11.0",
        "sha512-jYcgT6xtVYhnhgxh3QgYDnnNMYTcf8ElbxxFzX0IZo+vabQqSPAjC3c1wJrKB5E19VwQei89QCiZZP86DCPF7g==",
        {
            "package/lib/addon-fit.js": "addon-fit.js",
            "package/LICENSE": "LICENSE-addon-fit",
        },
    ),
    (
        "@xterm/addon-search",
        "0.16.0",
        "sha512-9OeuBFu0/uZJPu+9AHKY6g/w0Czyb/Ut0A5t79I4ULoU4IfU5BEpPFVGQxP4zTTMdfZEYkVIRYbHBX1xWwjeSA==",
        {
            "package/lib/addon-search.js": "addon-search.js",
            "package/LICENSE": "LICENSE-addon-search",
        },
    ),
)
STAMP = " ".join(f"{name}@{version}" for name, version, _, _ in PACKAGES)

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "src" / "nightexpress" / "resources" / "terminal" / "xterm"


def url_for(name: str, version: str) -> str:
    return f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{version}.tgz"


def verify(url: str, data: bytes, integrity: str) -> None:
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != integrity:
        sys.exit(f"integrity mismatch for {url}\n  expected {integrity}\n  got      {digest}")


def extract(data: bytes, files: dict[str, str], dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member_name, target_name in files.items():
            source = archive.extractfile(member_name)
            if source is None:
                sys.exit(f"{member_name} is missing in the archive")
            (dest / target_name).write_bytes(source.read())


def main() -> None:
    version_file = DEST / "VERSION"
    if version_file.is_file() and version_file.read_text().strip() == STAMP:
        print(f"xterm.js ({STAMP}) is already present in {DEST}")
        return
    staging = DEST.with_name("xterm.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name, version, integrity, files in PACKAGES:
        url = url_for(name, version)
        print(f"Downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        verify(url, data, integrity)
        extract(data, files, staging)
    (staging / "VERSION").write_text(STAMP + "\n")
    shutil.rmtree(DEST, ignore_errors=True)
    staging.rename(DEST)
    size = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"xterm.js ({STAMP}) installed in {DEST} ({size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
