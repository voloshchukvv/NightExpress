from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nightexpress.services.rustfmt import RustfmtError, format_rust, project_edition

ROOT = Path(__file__).resolve().parents[2]
RUSTFMT = shutil.which("rustfmt") or str(Path.home() / ".cargo" / "bin" / "rustfmt")
needs_rustfmt = pytest.mark.skipif(not Path(RUSTFMT).is_file(), reason="rustfmt not installed")


def test_project_edition(tmp_path: Path) -> None:
    assert project_edition(tmp_path) == "2021"  # no Cargo.toml
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nedition = "2024"\n')
    assert project_edition(tmp_path) == "2024"
    (tmp_path / "Cargo.toml").write_text("not [toml")
    assert project_edition(tmp_path) == "2021"


@needs_rustfmt
def test_formats_through_stdin_without_touching_files() -> None:
    project = ROOT / "reference" / "blinky_f103c6"
    before = (project / "src" / "main.rs").read_text()
    formatted = format_rust("fn main(){let x=1;   }\n", [RUSTFMT], project)
    assert formatted == "fn main() {\n    let x = 1;\n}\n"
    assert (project / "src" / "main.rs").read_text() == before


@needs_rustfmt
def test_syntax_error_reports_the_line() -> None:
    with pytest.raises(RustfmtError, match=r"рядок 2") as info:
        format_rust(
            "fn main() {\n    let x = ;\n}\n", [RUSTFMT], ROOT / "reference" / "blinky_f103c6"
        )
    assert info.value.line == 2
    assert "error:" not in str(info.value)


def test_missing_rustfmt(tmp_path: Path) -> None:
    with pytest.raises(RustfmtError, match="rustup component add rustfmt"):
        format_rust("fn main() {}\n", [str(tmp_path / "no-rustfmt")], tmp_path)
