"""AstExtractedReference → Resolution via live introspection.

For each reference, this module asks a KnowledgeBase "does this symbol exist
in the target module, and if so what's its signature?". The production KB
(SitePackagesKB) answers by running importlib.import_module against the
interpreter's site-packages and walking the dotted path with getattr.

A "known but not found" Resolution means the module was inspectable and the
symbol is definitely not there — a high-confidence hallucination. A
"known=False" Resolution means we couldn't introspect (package not installed,
import-time error) — forwarded to the LLM layer rather than flagged, so we
preserve the spec's 100%-precision promise.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .types import AstExtractedReference


# Packages the KB is allowed to freshly import (PR #26 security review).
# Untrusted diff content can name arbitrary modules; importing them runs
# their top-level code. Restrict to stdlib (always safe) plus a curated set
# of well-known packages that are likely to legitimately appear in Khati-
# corpus-style reviewable diffs. Anything else falls through to sys.modules
# lookup only (no fresh import) or is refused — see SitePackagesKB._import_module.
_DEFAULT_ALLOWED_PACKAGES: frozenset[str] = frozenset({
    # Khati 2026 corpus libraries
    "numpy", "pandas", "matplotlib", "requests",
    # Dev / toolchain deps already on the Phase 4b trust boundary
    "click", "tree_sitter", "tree_sitter_python", "unidiff",
    "pytest", "pytest_cov", "hypothesis", "coverage",
    # Package ecosystem adjacent
    "setuptools", "pip", "wheel", "packaging",
    # The package itself
    "hallucination_ast",
})


@dataclass
class Resolution:
    """Outcome of checking one AstExtractedReference against a KB."""
    found: bool
    known: bool
    signature: inspect.Signature | None = None
    is_deprecated: bool = False
    deprecation_message: str | None = None
    siblings: list[str] = field(default_factory=list)


class KnowledgeBase(Protocol):
    def lookup(self, module: str, symbol: str) -> Resolution:
        ...


# --- Public API ------------------------------------------------------------


def resolve(ref: AstExtractedReference, kb: KnowledgeBase) -> Resolution:
    """Ask the KB to resolve one reference."""
    module = ref.module or _first_segment(ref.symbol)
    return kb.lookup(module, ref.symbol)


# --- SitePackagesKB -------------------------------------------------------


class SitePackagesKB:
    """Production KB backed by the interpreter's installed packages.

    Caches imported modules so repeated lookups against the same package are
    cheap. Any exception during import marks the module as unknown rather
    than crashing the caller.
    """

    _SENTINEL_MISSING = object()

    def __init__(
        self,
        allowed_packages: Iterable[str] | None = None,
    ) -> None:
        """`allowed_packages` overrides the default curated allowlist. Pass
        `frozenset()` to lock it down to stdlib + pre-imported modules only."""
        self._module_cache: dict[str, Any] = {}
        self._allowed: frozenset[str] = (
            frozenset(allowed_packages)
            if allowed_packages is not None
            else _DEFAULT_ALLOWED_PACKAGES
        )

    def lookup(self, module: str, symbol: str) -> Resolution:
        mod = self._get_cached(module)
        if mod is self._SENTINEL_MISSING or mod is None:
            return Resolution(found=False, known=False)

        # Walk the dotted remainder past the module name.
        remainder = _strip_module_prefix(symbol, module)
        if not remainder:
            # `symbol == module` — the import itself. Known & found.
            return Resolution(found=True, known=True)

        obj: Any = mod
        qualified = module
        for part in remainder.split("."):
            qualified = f"{qualified}.{part}"
            if hasattr(obj, part):
                obj = getattr(obj, part)
                continue
            # Some packages don't eagerly expose submodules (e.g. matplotlib
            # until you `import matplotlib.pyplot`). Try importing the full
            # qualified path before declaring the symbol missing.
            submod = self._get_cached(qualified)
            if submod is not self._SENTINEL_MISSING and submod is not None:
                obj = submod
                continue
            siblings = sorted(_public_names(obj))
            return Resolution(found=False, known=True, siblings=siblings)

        # Leaf found. Try to read a signature; many C-extension callables
        # do not support inspect.signature — fall back to None.
        signature: inspect.Signature | None = None
        if callable(obj):
            try:
                signature = inspect.signature(obj)
            except (TypeError, ValueError):
                signature = None

        is_dep, dep_msg = _check_deprecated(obj)
        return Resolution(
            found=True,
            known=True,
            signature=signature,
            is_deprecated=is_dep,
            deprecation_message=dep_msg,
        )

    # Indirection point for tests — patched in test_site_packages_kb_caches_module_imports.
    def _import_module(self, name: str) -> Any:
        """Enforce the import allowlist before calling importlib.

        Rules:
          1. Already-imported modules (in sys.modules) are always returned —
             their side effects have already run, so there's no new risk.
          2. Stdlib roots and roots in the allowlist may be freshly imported.
          3. Anything else raises ImportError so the caller forwards to the
             LLM layer (known=False) rather than executing untrusted code.
        """
        if name in sys.modules:
            return sys.modules[name]
        root = name.split(".", 1)[0]
        if root not in sys.stdlib_module_names and root not in self._allowed:
            raise ImportError(
                f"{root!r} not in SitePackagesKB allowlist; refusing "
                f"fresh import of untrusted module"
            )
        return importlib.import_module(name)

    def _get_cached(self, name: str) -> Any:
        if name in self._module_cache:
            return self._module_cache[name]
        try:
            mod = self._import_module(name)
        except BaseException:
            # Broad catch: a broken dep might raise anything on import,
            # including SystemExit. We never want that to break the CLI.
            mod = self._SENTINEL_MISSING
        self._module_cache[name] = mod
        return mod


# --- helpers --------------------------------------------------------------


def _first_segment(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _strip_module_prefix(symbol: str, module: str) -> str:
    """Return the portion of `symbol` that lives inside `module`.

    For symbol="requests.get" and module="requests" returns "get".
    For symbol="os.path.join" and module="os.path" returns "join".
    For symbol=="module" returns "".
    """
    if symbol == module:
        return ""
    prefix = module + "."
    if symbol.startswith(prefix):
        return symbol[len(prefix):]
    # Symbol's module mismatches — caller's problem; treat as raw symbol so
    # we at least attempt a lookup rather than silently returning nothing.
    return symbol


def _public_names(obj: Any) -> list[str]:
    """Return candidate symbol names on `obj`, skipping dunders.

    Used for siblings lists fed into similarity.py."""
    try:
        names = dir(obj)
    except Exception:
        return []
    return [n for n in names if not n.startswith("_")]


def _check_deprecated(obj: Any) -> tuple[bool, str | None]:
    """Detect PEP 702 style deprecation markers.

    Checks in order:
      1. __deprecated__ attribute (PEP 702 / Python 3.13+ typing.deprecated).
      2. typing.deprecated wrapper surfaces the message on __deprecated__.
    Returns (is_deprecated, message).
    """
    msg = getattr(obj, "__deprecated__", None)
    if msg:
        return True, str(msg) if not isinstance(msg, bool) else None
    return False, None
