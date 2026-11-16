#!/usr/bin/env python3
"""Refresh the bundled icons from pinned npm packages (integrity-checked).

- Lucide (ISC): outline UI icons, written to resources/icons/lucide/
- Simple Icons (CC0-1.0): the Rust logo for the toolchain status, resources/icons/brand/

The SVGs are small and committed to git; run this only to add icons or bump versions.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "src" / "nightexpress" / "resources" / "icons"

PACKAGES = {
    "lucide-static": ("1.66.0", "lucide"),
    "simple-icons": ("16.36.0", "brand"),
}

LUCIDE = (
    "house", "folder", "folder-open", "settings", "plus", "cpu", "hammer", "play", "link",
    "unlink", "triangle-alert", "download", "square-terminal", "layers", "crosshair", "file",
    "file-code", "x", "circle-check", "circle-x", "circle-alert", "chevron-down",
    "chevron-right", "square", "refresh-cw", "usb", "search", "info", "trash-2", "eye",
    "terminal", "file-plus", "bug", "ellipsis", "file-text", "lock", "memory-stick",
    "folder-plus", "wrap-text", "copy", "chevron-up", "pause", "save", "external-link",
    "lightbulb", "message-square-text", "mouse-pointer-click", "circle-dashed", "folder-x",
)  # fmt: skip
SIMPLE_ICONS = ("rust",)


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def package(name: str, version: str) -> tarfile.TarFile:
    meta = json.loads(fetch(f"https://registry.npmjs.org/{name}/{version}"))
    data = fetch(meta["dist"]["tarball"])
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != meta["dist"]["integrity"]:
        sys.exit(f"integrity mismatch for {name}@{version}")
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def extract(archive: tarfile.TarFile, member: str, target: Path) -> None:
    source = archive.extractfile(member)
    if source is None:
        sys.exit(f"{member} not found")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read())


def main() -> None:
    version, folder = PACKAGES["lucide-static"]
    with package("lucide-static", version) as archive:
        for icon in LUCIDE:
            extract(archive, f"package/icons/{icon}.svg", ICONS / folder / f"{icon}.svg")
        extract(archive, "package/LICENSE", ICONS / folder / "LICENSE")
    version, folder = PACKAGES["simple-icons"]
    with package("simple-icons", version) as archive:
        for icon in SIMPLE_ICONS:
            extract(archive, f"package/icons/{icon}.svg", ICONS / folder / f"{icon}.svg")
        extract(archive, "package/LICENSE.md", ICONS / folder / "LICENSE.md")
    print(f"icons updated in {ICONS}")


if __name__ == "__main__":
    main()
