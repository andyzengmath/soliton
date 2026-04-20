"""Tests for hallucination_ast.extract.

Two APIs tested:
  - extract_from_source(source, file_path) -> list[AstExtractedReference]
      Core primitive. Parses a Python source string, walks the AST, emits refs.
  - extract_from_diff(diff_text, get_post_image) -> list[AstExtractedReference]
      Wraps the primitive. Loads each changed file's post-image, filters refs
      to those on added ('+') lines.

Scope for v0.1: import, call, method, attribute kinds. Type annotations and
decorators are 4b.x follow-ups (tracked in Rule literal).
"""
from __future__ import annotations

import pytest


# --- extract_from_source ---------------------------------------------------


def test_simple_import_one_ref():
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("import requests\n", "foo.py")
    assert len(refs) == 1
    r = refs[0]
    assert r.kind == "import"
    assert r.symbol == "requests"
    assert r.module == "requests"
    assert r.file == "foo.py"
    assert r.line == 1


def test_dotted_import_preserves_full_path():
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("import os.path\n", "foo.py")
    assert len(refs) == 1
    assert refs[0].kind == "import"
    assert refs[0].symbol == "os.path"
    assert refs[0].module == "os"


def test_import_as_records_source_not_alias():
    """`import requests as r` — the thing to validate is `requests`, not `r`."""
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("import requests as r\n", "foo.py")
    assert len(refs) == 1
    assert refs[0].symbol == "requests"
    assert refs[0].module == "requests"


def test_from_import_records_each_name():
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("from requests import get, post\n", "foo.py")
    # Two names imported → two refs, both with module=requests.
    syms = sorted(r.symbol for r in refs if r.kind == "import")
    assert syms == ["requests.get", "requests.post"]
    for r in refs:
        if r.kind == "import":
            assert r.module == "requests"


def test_from_import_star_records_module_only():
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("from requests import *\n", "foo.py")
    imports = [r for r in refs if r.kind == "import"]
    assert len(imports) == 1
    assert imports[0].symbol == "requests"
    assert imports[0].module == "requests"


def test_bare_function_call():
    from hallucination_ast.extract import extract_from_source

    refs = extract_from_source("foo()\n", "a.py")
    calls = [r for r in refs if r.kind == "call"]
    assert len(calls) == 1
    c = calls[0]
    assert c.symbol == "foo"
    assert c.module is None
    assert c.arg_count == 0
    assert c.kwargs == []


def test_method_call_on_imported_module():
    """The Khati 2026 core case: `requests.get("u")`."""
    from hallucination_ast.extract import extract_from_source

    src = 'import requests\nrequests.get("u")\n'
    refs = extract_from_source(src, "a.py")

    methods = [r for r in refs if r.kind == "method"]
    assert len(methods) == 1
    m = methods[0]
    assert m.symbol == "requests.get"
    assert m.module == "requests"
    assert m.arg_count == 1
    assert m.kwargs == []


def test_method_call_records_kwargs():
    from hallucination_ast.extract import extract_from_source

    src = 'import requests\nrequests.get("u", timeout=10, allow_redirects=False)\n'
    refs = extract_from_source(src, "a.py")
    methods = [r for r in refs if r.kind == "method"]
    assert len(methods) == 1
    m = methods[0]
    assert m.arg_count == 1  # only "u" is positional
    assert sorted(m.kwargs) == ["allow_redirects", "timeout"]


def test_method_call_arg_count_ignores_stars():
    """*args and **kwargs are not resolvable statically; don't count as positional."""
    from hallucination_ast.extract import extract_from_source

    src = "import x\nx.f(1, 2, *rest, k=1, **more)\n"
    refs = extract_from_source(src, "a.py")
    methods = [r for r in refs if r.kind == "method"]
    assert len(methods) == 1
    # arg_count is None when *args splats are present — signature can't be checked.
    assert methods[0].arg_count is None


def test_attribute_access_not_called():
    from hallucination_ast.extract import extract_from_source

    src = "import requests\nx = requests.DEFAULT_TIMEOUT\n"
    refs = extract_from_source(src, "a.py")
    attrs = [r for r in refs if r.kind == "attribute"]
    assert len(attrs) == 1
    a = attrs[0]
    assert a.symbol == "requests.DEFAULT_TIMEOUT"
    assert a.module == "requests"


def test_nested_attribute_access():
    """`a.b.c` is one attribute ref with the fully-dotted symbol."""
    from hallucination_ast.extract import extract_from_source

    src = "import a\nx = a.b.c\n"
    refs = extract_from_source(src, "x.py")
    attrs = [r for r in refs if r.kind == "attribute"]
    assert len(attrs) == 1
    assert attrs[0].symbol == "a.b.c"
    assert attrs[0].module == "a"


def test_method_call_does_not_also_emit_attribute():
    """When `requests.get(...)` is a call, we emit it as kind=method only —
    not separately as an attribute read. Otherwise checkers double-count."""
    from hallucination_ast.extract import extract_from_source

    src = 'import requests\nrequests.get("u")\n'
    refs = extract_from_source(src, "a.py")
    kinds = [r.kind for r in refs]
    assert kinds.count("method") == 1
    # Zero naked attribute refs for this source.
    assert kinds.count("attribute") == 0


def test_line_and_column_accuracy():
    from hallucination_ast.extract import extract_from_source

    src = "import requests\n\nrequests.get('u')\n"
    refs = extract_from_source(src, "a.py")
    method = next(r for r in refs if r.kind == "method")
    assert method.line == 3  # 1-indexed line where `requests.get(` appears
    assert method.column == 0


def test_empty_source_returns_empty():
    from hallucination_ast.extract import extract_from_source

    assert extract_from_source("", "a.py") == []


def test_comments_and_strings_ignored():
    from hallucination_ast.extract import extract_from_source

    src = '# import requests\nx = "requests.get"\n'
    refs = extract_from_source(src, "a.py")
    assert refs == []


def test_local_function_call_has_no_module():
    from hallucination_ast.extract import extract_from_source

    src = "def foo(): pass\nfoo()\n"
    refs = extract_from_source(src, "a.py")
    calls = [r for r in refs if r.kind == "call"]
    assert len(calls) == 1
    assert calls[0].symbol == "foo"
    assert calls[0].module is None


# --- extract_from_diff -----------------------------------------------------


def _synth_diff(path: str, added_lines: list[str], old_lines: list[str] | None = None) -> str:
    """Build a minimal unified-diff string for one file.

    Uses the `/dev/null` source when old_lines is None (new-file creation).
    """
    old_lines = old_lines or []
    header = (
        f"--- a/{path}\n+++ b/{path}\n"
        if old_lines
        else f"--- /dev/null\n+++ b/{path}\n"
    )
    hunk_header = f"@@ -0,0 +1,{len(added_lines)} @@\n" if not old_lines else (
        f"@@ -1,{len(old_lines)} +1,{len(added_lines)} @@\n"
    )
    body = "".join(f"+{line}\n" for line in added_lines)
    return header + hunk_header + body


def test_diff_new_file_extracts_all_refs():
    from hallucination_ast.extract import extract_from_diff

    diff = _synth_diff("foo.py", ["import requests", "requests.get('u')"])
    refs = extract_from_diff(diff, repo_root=None)
    syms = sorted({r.symbol for r in refs})
    assert "requests" in syms
    assert "requests.get" in syms


def test_diff_only_added_lines_extracted(tmp_path):
    """If a file existed before with `import os` on line 1, and the diff adds
    `requests.get('u')` on line 3, the existing `import os` should NOT appear
    in the extracted refs — only the added-line references."""
    from hallucination_ast.extract import extract_from_diff

    # Simulate: old file had `import os`, new file added requests usage.
    foo = tmp_path / "foo.py"
    foo.write_text("import os\nimport requests\nrequests.get('u')\n")

    diff = (
        f"--- a/foo.py\n+++ b/foo.py\n"
        f"@@ -1,1 +1,3 @@\n"
        f" import os\n"
        f"+import requests\n"
        f"+requests.get('u')\n"
    )
    refs = extract_from_diff(diff, repo_root=tmp_path)
    syms = sorted({r.symbol for r in refs})
    # The context-line `import os` must not be in output.
    assert "os" not in syms
    assert "requests" in syms
    assert "requests.get" in syms


def test_diff_ignores_non_python_files(tmp_path):
    from hallucination_ast.extract import extract_from_diff

    diff = (
        "--- /dev/null\n+++ b/README.md\n@@ -0,0 +1,1 @@\n+import requests\n"
    )
    refs = extract_from_diff(diff, repo_root=tmp_path)
    assert refs == []
