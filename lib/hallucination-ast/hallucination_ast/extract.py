"""Diff → AstExtractedReference[] via tree-sitter-python.

Two public entry points:

- extract_from_source(source, file_path)
    Parse a full Python source string and yield every import / call / method /
    attribute reference. Used directly against standalone files (Khati corpus)
    and indirectly by extract_from_diff.

- extract_from_diff(diff_text, repo_root)
    Parse a unified diff, load each changed Python file's post-image (from
    repo_root if the file exists on disk; otherwise from the '+' lines of the
    diff), extract refs from the whole file, then filter to references that
    fall on added lines. Context-line references are dropped.

Out of scope for v0.1:
    - type annotations (`def f(x: T) -> U: ...`) — kind 'type' tracked in
      the Rule literal but not emitted yet. Khati's corpus is dominated by
      imports + calls so this does not affect the shipping gate.
    - decorators — same rationale.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from tree_sitter import Language, Node, Parser
import tree_sitter_python as _tspy
import unidiff

from .types import AstExtractedReference, ImportInfo


_LANGUAGE = Language(_tspy.language())
_PARSER = Parser(_LANGUAGE)


# --- public API ------------------------------------------------------------


def extract_from_source(source: str, file_path: str) -> list[AstExtractedReference]:
    """Parse a Python source string and return every reference we can extract."""
    if not source:
        return []
    tree = _PARSER.parse(source.encode("utf-8"))
    skip_ids: set[int] = set()
    return list(_walk(tree.root_node, file_path, skip_ids))


def extract_imports_info(source: str) -> ImportInfo:
    """Parse only top-level import statements and return alias/root info.

    Shares the tree-sitter parser but walks just the module-level import
    constructs. Nested / conditional imports are intentionally ignored —
    our heuristics only care whether a name was unconditionally bound.
    """
    info = ImportInfo()
    if not source:
        return info
    tree = _PARSER.parse(source.encode("utf-8"))
    for child in tree.root_node.children:
        if child.type == "import_statement":
            _record_import_statement(child, info)
        elif child.type == "import_from_statement":
            _record_from_import_statement(child, info)
    return info


def _record_import_statement(node: Node, info: ImportInfo) -> None:
    for c in node.children:
        if c.type == "dotted_name":
            name = _dotted_name(c)
            if name:
                info.imported_roots.add(_first_segment(name))
        elif c.type == "aliased_import":
            name_node = c.child_by_field_name("name")
            alias_node = c.child_by_field_name("alias")
            name = _dotted_name(name_node) if name_node else None
            alias = _text(alias_node) if alias_node else None
            if name:
                info.imported_roots.add(_first_segment(name))
            if name and alias:
                info.alias_to_module[alias] = name


def _record_from_import_statement(node: Node, info: ImportInfo) -> None:
    module_node = node.child_by_field_name("module_name")
    module_name = _dotted_name(module_node) if module_node else None
    if module_name:
        info.imported_roots.add(_first_segment(module_name))
    for child in node.children_by_field_name("name"):
        if child.type == "dotted_name":
            leaf = _dotted_name(child)
            if leaf:
                info.imported_roots.add(_first_segment(leaf))
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            leaf = _dotted_name(name_node) if name_node else None
            alias = _text(alias_node) if alias_node else None
            if alias:
                info.imported_roots.add(alias)
                # Track the alias -> full qualified name (module.leaf).
                if leaf and module_name:
                    info.alias_to_module[alias] = f"{module_name}.{leaf}"
            elif leaf:
                info.imported_roots.add(leaf)


def extract_from_diff(
    diff_text: str,
    repo_root: Path | None = None,
) -> list[AstExtractedReference]:
    """Extract references from added lines of the unified diff.

    For each changed .py file we need a parseable post-image. If repo_root is
    provided and the file exists on disk we read it (cheap, correct). Otherwise
    we synthesize the post-image from the diff's context + '+' lines.
    """
    if not diff_text.strip():
        return []

    try:
        patch = unidiff.PatchSet(diff_text)
    except Exception:
        return []

    out: list[AstExtractedReference] = []
    for patched_file in patch:
        target = patched_file.target_file or ""
        # unidiff prefixes target with "b/". Strip.
        if target.startswith("b/"):
            target = target[2:]
        if not target.endswith(".py"):
            continue

        post_image = _load_post_image(patched_file, target, repo_root)
        if post_image is None:
            continue

        added_lines = _added_line_numbers(patched_file)

        for ref in extract_from_source(post_image, target):
            if ref.line in added_lines:
                out.append(ref)
    return out


# --- diff helpers ----------------------------------------------------------


def _load_post_image(
    patched_file: unidiff.PatchedFile,
    target: str,
    repo_root: Path | None,
) -> str | None:
    """Best-effort load of the post-image source text for a changed file."""
    if repo_root is not None:
        on_disk = repo_root / target
        if on_disk.is_file():
            try:
                return on_disk.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
    # Synthesize from the diff itself — works cleanly for newly-added files
    # where every line is a '+' line. For modified files without repo_root,
    # the result is a partial file but still parseable enough for the
    # token-level extractors.
    fragments: list[str] = []
    for hunk in patched_file:
        for line in hunk:
            if line.is_added or line.is_context:
                fragments.append(line.value)
    if not fragments:
        return None
    return "".join(fragments)


def _added_line_numbers(patched_file: unidiff.PatchedFile) -> set[int]:
    numbers: set[int] = set()
    for hunk in patched_file:
        for line in hunk:
            if line.is_added and line.target_line_no is not None:
                numbers.add(line.target_line_no)
    return numbers


# --- tree-sitter walker ----------------------------------------------------


def _text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


def _dotted_name(node: Node | None) -> str | None:
    """Return the fully-qualified dotted name for a pure identifier/attribute
    chain, or None if the node contains anything else (calls, subscripts, …)."""
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type == "dotted_name":
        # `import X.Y.Z` — child identifiers joined by '.'.
        parts: list[str] = []
        for child in node.children:
            if child.type == "identifier":
                parts.append(_text(child))
        return ".".join(parts) if parts else None
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        obj_name = _dotted_name(obj)
        if obj_name is None or attr is None:
            return None
        return f"{obj_name}.{_text(attr)}"
    return None


def _mark_dotted_chain(node: Node | None, skip_ids: set[int]) -> None:
    """Mark the identifier/attribute chain as consumed so descent into it
    doesn't re-emit pieces as separate refs."""
    if node is None:
        return
    skip_ids.add(node.id)
    for child in node.children:
        skip_ids.add(child.id)
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        _mark_dotted_chain(obj, skip_ids)


def _first_segment(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _walk(
    node: Node,
    file_path: str,
    skip_ids: set[int],
) -> Iterable[AstExtractedReference]:
    if node.id in skip_ids:
        return

    t = node.type

    if t == "import_statement":
        yield from _emit_import(node, file_path)
        return
    if t == "import_from_statement":
        yield from _emit_from_import(node, file_path)
        return
    if t == "call":
        ref = _emit_call(node, file_path, skip_ids)
        if ref is not None:
            yield ref
    elif t == "attribute":
        # Emit only the top of a pure dotted-name chain, and not when it is
        # the `function` field of a call (call handler owns that).
        parent = node.parent
        is_mid_chain = (
            parent is not None
            and parent.type == "attribute"
            and parent.child_by_field_name("object") is node
        )
        is_call_func = (
            parent is not None
            and parent.type == "call"
            and parent.child_by_field_name("function") is node
        )
        if not is_mid_chain and not is_call_func:
            dotted = _dotted_name(node)
            if dotted is not None:
                yield AstExtractedReference(
                    kind="attribute",
                    file=file_path,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                    symbol=dotted,
                    module=_first_segment(dotted),
                )
                _mark_dotted_chain(node, skip_ids)

    for child in node.children:
        yield from _walk(child, file_path, skip_ids)


# --- emitters --------------------------------------------------------------


def _emit_import(node: Node, file_path: str) -> Iterable[AstExtractedReference]:
    """Handle `import X`, `import X.Y`, `import X as Y`, `import X, Y`."""
    for child in node.children:
        if child.type == "dotted_name":
            dotted = _dotted_name(child)
            if dotted:
                yield AstExtractedReference(
                    kind="import",
                    file=file_path,
                    line=child.start_point[0] + 1,
                    column=child.start_point[1],
                    symbol=dotted,
                    module=_first_segment(dotted),
                )
        elif child.type == "aliased_import":
            # Structure: aliased_import(name=dotted_name, alias=identifier).
            name = child.child_by_field_name("name")
            dotted = _dotted_name(name)
            if dotted:
                yield AstExtractedReference(
                    kind="import",
                    file=file_path,
                    line=child.start_point[0] + 1,
                    column=child.start_point[1],
                    symbol=dotted,
                    module=_first_segment(dotted),
                )


def _emit_from_import(
    node: Node, file_path: str
) -> Iterable[AstExtractedReference]:
    """Handle `from X import a, b as c, *`."""
    module_node = node.child_by_field_name("module_name")
    module_name = _dotted_name(module_node) if module_node else None
    if not module_name:
        return

    # Check for wildcard import: a `wildcard_import` child.
    has_wildcard = any(
        child.type == "wildcard_import" for child in node.children
    )
    if has_wildcard:
        yield AstExtractedReference(
            kind="import",
            file=file_path,
            line=node.start_point[0] + 1,
            column=node.start_point[1],
            symbol=module_name,
            module=module_name,
        )
        return

    # Each `name` field child is either a dotted_name or an aliased_import.
    for child in node.children_by_field_name("name"):
        if child.type == "dotted_name":
            sym = _dotted_name(child)
        elif child.type == "aliased_import":
            sym = _dotted_name(child.child_by_field_name("name"))
        else:
            sym = None
        if not sym:
            continue
        yield AstExtractedReference(
            kind="import",
            file=file_path,
            line=child.start_point[0] + 1,
            column=child.start_point[1],
            symbol=f"{module_name}.{sym}",
            module=module_name,
        )


def _emit_call(
    node: Node,
    file_path: str,
    skip_ids: set[int],
) -> AstExtractedReference | None:
    """Handle `f(args)` and `obj.method(args)`."""
    func = node.child_by_field_name("function")
    if func is None:
        return None

    dotted = _dotted_name(func)
    if dotted is None:
        # Function is a complex expression (another call, subscript, …).
        # Can't statically validate; skip.
        return None

    kind = "method" if "." in dotted else "call"
    module = _first_segment(dotted) if "." in dotted else None

    args_node = node.child_by_field_name("arguments")
    arg_count, kwargs = _count_args(args_node)

    # Mark the function's dotted chain as consumed so we don't re-emit it
    # as a separate attribute ref during later descent.
    _mark_dotted_chain(func, skip_ids)

    return AstExtractedReference(
        kind=kind,
        file=file_path,
        line=node.start_point[0] + 1,
        column=node.start_point[1],
        symbol=dotted,
        module=module,
        arg_count=arg_count,
        kwargs=kwargs,
    )


def _count_args(args_node: Node | None) -> tuple[int | None, list[str]]:
    """Return (positional_arg_count, list_of_kwarg_names).

    If the call uses *splat or **splat we can't verify arity statically, so
    return (None, …) to signal that arity checking should be skipped.
    """
    if args_node is None:
        return 0, []

    positional = 0
    kwargs: list[str] = []
    has_splat = False

    for child in args_node.children:
        t = child.type
        if t in ("(", ")", ","):
            continue
        if t == "keyword_argument":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                kwargs.append(_text(name_node))
        elif t in ("list_splat", "dictionary_splat",
                   "list_splat_pattern", "dictionary_splat_pattern"):
            has_splat = True
        elif t.startswith("comment"):
            continue
        else:
            positional += 1

    arg_count: int | None = None if has_splat else positional
    return arg_count, kwargs
