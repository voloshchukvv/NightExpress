"""Automated check of the MVC layering rules.

Every module of the package is parsed with AST; imports (absolute and relative) are resolved
to a path inside the package and checked against the deny tables below.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "nightexpress"
ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / PACKAGE
# Not the project's own documents: local notes, downloaded or generated files.
IGNORED_DIRECTORIES = {
    "dev",
    ".venv",
    ".git",
    "target",
    "monaco",
    "xterm",
    "node_modules",
    ".pytest_cache",
    ".idea",
}

# Layer -> internal package modules it must not import.
FORBIDDEN_INTERNAL: dict[str, tuple[str, ...]] = {
    "models": ("views", "controllers", "services", "app"),
    "views": ("services", "controllers", "app"),
    "services": ("views", "controllers", "app", "models.state", "models.qt_models"),
    "controllers": ("app",),
}

# Layer -> external modules it must not import.
FORBIDDEN_EXTERNAL: dict[str, tuple[str, ...]] = {
    "models.entities": ("PySide6",),
    "models.state": ("PySide6.QtWidgets", "PySide6.QtGui"),
    "services": ("PySide6.QtWidgets", "PySide6.QtGui"),
}


@dataclass(frozen=True)
class Violation:
    module: str
    line: int
    imported: str
    rule: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} imports {self.imported} (forbidden: {self.rule})"


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _matches(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _imports(tree: ast.AST, module: str, is_package: bool, package: str) -> list[tuple[int, str]]:
    """Return (line, name) imports; internal ones are fully qualified, external ones as-is."""
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".") if module else []
                if not is_package:
                    base = base[:-1]
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([package, *base])
            else:
                prefix = ""
            target = ".".join(p for p in (prefix, node.module or "") if p)
            result.append((node.lineno, target))
            result.extend((node.lineno, f"{target}.{alias.name}") for alias in node.names)
    return result


def find_violations(root: Path, package: str) -> list[Violation]:
    found: dict[tuple[str, int, str], Violation] = {}
    for path in sorted(root.rglob("*.py")):
        module = _module_name(path, root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, imported in _imports(tree, module, path.name == "__init__.py", package):
            if _matches(imported, package):
                internal = imported[len(package) + 1 :]
                for layer, forbidden in FORBIDDEN_INTERNAL.items():
                    if not _matches(module, layer):
                        continue
                    for f in forbidden:
                        if internal and _matches(internal, f):
                            rule = f"{layer} → {f}"
                            found.setdefault(
                                (module, line, rule), Violation(module, line, imported, rule)
                            )
            else:
                for layer, forbidden in FORBIDDEN_EXTERNAL.items():
                    if not _matches(module, layer):
                        continue
                    for f in forbidden:
                        if _matches(imported, f):
                            rule = f"{layer} → {f}"
                            found.setdefault(
                                (module, line, rule), Violation(module, line, imported, rule)
                            )
    return sorted(found.values(), key=str)


def test_package_follows_mvc_rules() -> None:
    violations = find_violations(PACKAGE_ROOT, PACKAGE)
    assert not violations, "MVC violations:\n" + "\n".join(map(str, violations))


def _make_pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    for rel, code in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(code, encoding="utf-8")
    for d in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        (d / "__init__.py").touch()
    return root


def test_detects_absolute_import_violation(tmp_path: Path) -> None:
    root = _make_pkg(
        tmp_path, {"models/state/m.py": "from pkg.views.w import W\n", "views/w.py": ""}
    )
    assert [v.rule for v in find_violations(root, "pkg")] == ["models → views"]


def test_detects_relative_import_violation(tmp_path: Path) -> None:
    root = _make_pkg(tmp_path, {"views/w.py": "from ..services import cargo\n"})
    rules = {v.rule for v in find_violations(root, "pkg")}
    assert rules == {"views → services"}


def test_detects_from_package_import_name(tmp_path: Path) -> None:
    root = _make_pkg(tmp_path, {"services/s.py": "from pkg import controllers\n"})
    assert {v.rule for v in find_violations(root, "pkg")} == {"services → controllers"}


def test_detects_qt_in_entities_and_widgets_in_services(tmp_path: Path) -> None:
    root = _make_pkg(
        tmp_path,
        {
            "models/entities/e.py": "from PySide6.QtCore import QObject\n",
            "services/s.py": "import PySide6.QtWidgets\nfrom PySide6.QtCore import QProcess\n",
        },
    )
    rules = {v.rule for v in find_violations(root, "pkg")}
    assert rules == {"models.entities → PySide6", "services → PySide6.QtWidgets"}


def test_allowed_imports_pass(tmp_path: Path) -> None:
    root = _make_pkg(
        tmp_path,
        {
            "controllers/c.py": "from pkg.services import s\nfrom pkg.views import v\n",
            "views/v.py": "from pkg.models.state import m\nfrom PySide6.QtWidgets import QWidget\n",
            "services/s.py": (
                "from pkg.models.entities import e\nfrom PySide6.QtCore import QProcess\n"
            ),
            "models/state/m.py": "from PySide6.QtCore import QObject, Signal\n",
            "models/entities/e.py": "from dataclasses import dataclass\n",
        },
    )
    assert find_violations(root, "pkg") == []


CYRILLIC = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")


def _python_files() -> list[Path]:
    return [p for folder in ("src", "tests", "scripts") for p in (ROOT / folder).rglob("*.py")]


def test_comments_and_docstrings_are_in_english() -> None:
    """Texts for the user are Ukrainian (the translation key); notes for developers are not."""
    import io
    import tokenize

    found = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and CYRILLIC.search(token.string):
                found.append(f"{path.relative_to(ROOT)}:{token.start[0]}")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                docstring = ast.get_docstring(node, clean=False)
                if docstring and CYRILLIC.search(docstring):
                    found.append(f"{path.relative_to(ROOT)}:{node.body[0].lineno}")
    assert found == []


def test_project_documents_are_in_english() -> None:
    """The project's own Markdown (not the templates of generated projects); code
    spans may quote data, e.g. a name table of a theme."""
    documents = [p for p in ROOT.rglob("*.md") if not set(p.parts) & IGNORED_DIRECTORIES]
    found = []
    for path in documents:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CYRILLIC.search(re.sub(r"`[^`]*`", "", line)):
                found.append(f"{path.relative_to(ROOT)}:{number}")
    assert documents and found == []
