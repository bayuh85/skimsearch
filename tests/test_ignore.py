"""Ignore rules for dependency trees, secrets, and repo .gitignore files."""

from pathlib import Path

from skimsearch.ignore import IgnoreSet


def test_defaults_skip_dependencies_and_secrets(tmp_path: Path):
    root = tmp_path
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
    ignore = IgnoreSet(root)
    assert ignore.ignored("node_modules", True)
    assert ignore.ignored("node_modules/pkg.js", False)
    assert ignore.ignored("services/api/node_modules/pkg.js", False)
    assert ignore.ignored(".env", False)
    assert ignore.ignored(".env.local", False)
    assert not ignore.ignored("src/app.py", False)
    assert not ignore.ignored("src", True)


def test_gitignore_vendor_and_negation(tmp_path: Path):
    root = tmp_path
    (root / ".gitignore").write_text("vendor/\n*.log\n!keep.log\n", encoding="utf-8")
    ignore = IgnoreSet(root)
    assert ignore.ignored("vendor/leftpad/index.js", False)
    assert ignore.ignored("src/vendor/pkg/index.js", False)
    assert ignore.ignored("debug.log", False)
    assert ignore.ignored("pkg/debug.log", False)
    assert not ignore.ignored("keep.log", False)
    assert not ignore.ignored("pkg/keep.log", False)


def test_nested_gitignore_is_scoped(tmp_path: Path):
    root = tmp_path
    (root / "notes").mkdir()
    (root / "notes" / ".gitignore").write_text("secret.md\n", encoding="utf-8")
    (root / "other").mkdir()
    ignore = IgnoreSet(root)
    ignore.push(root / "notes", "notes")
    assert ignore.ignored("notes/secret.md", False)
    assert not ignore.ignored("notes/public.md", False)
    assert not ignore.ignored("other/secret.md", False)


def test_skimignore_can_put_a_build_dir_back(tmp_path: Path):
    root = tmp_path
    (root / ".skimignore").write_text("!dist/\n!dist/**\n", encoding="utf-8")
    ignore = IgnoreSet(root)
    assert not ignore.ignored("dist", True)
    assert not ignore.ignored("dist/docs/index.html", False)
