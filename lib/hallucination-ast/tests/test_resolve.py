"""Tests for hallucination_ast.resolve.

Two layers:
  - FakeKB unit tests — verify the resolve() function surfaces the KB
    response correctly without pulling in real packages.
  - SitePackagesKB integration tests — against numpy + requests (both
    guaranteed installed in the Soliton dev env); kept minimal to keep
    the suite fast and sandbox-friendly.
"""
from __future__ import annotations

import inspect

import pytest

from hallucination_ast.types import AstExtractedReference


# --- FakeKB helpers --------------------------------------------------------


class FakeKB:
    """Hand-authored KB for deterministic unit tests.

    Format: {module_name: {symbol_suffix: FakeEntry}}
    symbol_suffix is what follows the module name in a fully-dotted symbol,
    e.g. for "requests.get" the suffix is "get". Empty string matches the
    module itself.
    """

    def __init__(self, entries: dict, known_modules: set[str] | None = None):
        self._entries = entries
        # A module is "known" to the KB when any entry exists for it or it is
        # explicitly listed. `known=True, found=False` is the hallucination case.
        self._known = known_modules if known_modules is not None else set(entries)

    def lookup(self, module, symbol):
        from hallucination_ast.resolve import Resolution

        if module not in self._known:
            return Resolution(found=False, known=False)
        entries = self._entries.get(module, {})
        if symbol == module:
            # The module itself: always findable when known.
            return Resolution(found=True, known=True, siblings=sorted(entries.keys()))
        suffix = symbol[len(module) + 1:]
        if suffix not in entries:
            return Resolution(
                found=False,
                known=True,
                siblings=sorted(entries.keys()),
            )
        entry = entries[suffix]
        return Resolution(
            found=True,
            known=True,
            signature=entry.get("signature"),
            is_deprecated=entry.get("is_deprecated", False),
            deprecation_message=entry.get("deprecation_message"),
            siblings=sorted(entries.keys()),
        )


def _ref(symbol, module=None, kind="call", arg_count=None, kwargs=None):
    return AstExtractedReference(
        kind=kind,
        file="x.py",
        line=1,
        column=0,
        symbol=symbol,
        module=module or symbol.split(".", 1)[0],
        arg_count=arg_count,
        kwargs=kwargs,
    )


# --- FakeKB unit tests ----------------------------------------------------


def test_resolution_dataclass_defaults():
    from hallucination_ast.resolve import Resolution

    r = Resolution(found=False, known=False)
    assert r.signature is None
    assert r.is_deprecated is False
    assert r.deprecation_message is None
    assert r.siblings == []


def test_resolve_known_symbol_returns_found():
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"requests": {"get": {"signature": None}}})
    res = resolve(_ref("requests.get", module="requests"), kb)
    assert res.known is True
    assert res.found is True


def test_resolve_typo_symbol_returns_not_found_with_siblings():
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"requests": {"get": {}, "post": {}, "put": {}}})
    res = resolve(_ref("requests.gett", module="requests"), kb)
    assert res.known is True
    assert res.found is False
    assert "get" in res.siblings


def test_resolve_unknown_module_returns_known_false():
    """Missing package → forward to LLM; not a hallucination by itself."""
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"requests": {"get": {}}}, known_modules={"requests"})
    res = resolve(_ref("totally_fake_pkg.do_stuff", module="totally_fake_pkg"), kb)
    assert res.known is False
    assert res.found is False


def test_resolve_surfaces_signature_when_present():
    from hallucination_ast.resolve import resolve

    def fake_get(url, timeout=None): ...

    sig = inspect.signature(fake_get)
    kb = FakeKB({"requests": {"get": {"signature": sig}}})
    res = resolve(_ref("requests.get", module="requests"), kb)
    assert res.signature is sig


def test_resolve_surfaces_deprecation():
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"fs": {"exists": {"is_deprecated": True,
                                    "deprecation_message": "use fs.access"}}})
    res = resolve(_ref("fs.exists", module="fs"), kb)
    assert res.is_deprecated is True
    assert res.deprecation_message == "use fs.access"


def test_resolve_uses_module_from_ref_not_first_segment_of_symbol():
    """Edge case: `from X.Y import a` extracts symbol='X.Y.a' with module='X.Y'.
    Resolver must use ref.module, not symbol.split('.', 1)[0]."""
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"os.path": {"join": {}}})
    res = resolve(
        _ref("os.path.join", module="os.path", kind="import"),
        kb,
    )
    assert res.known is True
    assert res.found is True


def test_resolve_module_itself_found_when_known():
    """An `import requests` ref has symbol=='requests'==module; should resolve."""
    from hallucination_ast.resolve import resolve

    kb = FakeKB({"requests": {"get": {}}})
    res = resolve(_ref("requests", module="requests", kind="import"), kb)
    assert res.known is True
    assert res.found is True


# --- SitePackagesKB integration ------------------------------------------


def test_site_packages_kb_resolves_real_requests_get():
    """requests.get is a real function — KB must confirm it."""
    pytest.importorskip("requests")
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(_ref("requests.get", module="requests"), kb)
    assert res.known is True
    assert res.found is True
    # Signature should be readable for a plain Python function.
    assert res.signature is not None


def test_site_packages_kb_rejects_requests_gett_as_hallucination():
    """requests.gett is the classic hallucination — KB must reject it."""
    pytest.importorskip("requests")
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(_ref("requests.gett", module="requests"), kb)
    assert res.known is True
    assert res.found is False
    # Real "get" should appear among siblings so similarity can suggest it.
    assert "get" in res.siblings


def test_site_packages_kb_marks_uninstalled_module_as_unknown():
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(
        _ref("definitely_not_installed_pkg.thing",
             module="definitely_not_installed_pkg"),
        kb,
    )
    assert res.known is False


def test_site_packages_kb_caches_module_imports():
    """Two lookups on the same module should import_module only once."""
    pytest.importorskip("requests")
    from hallucination_ast.resolve import SitePackagesKB, resolve

    calls: list[str] = []
    kb = SitePackagesKB()
    original = kb._import_module
    kb._import_module = lambda name: (calls.append(name), original(name))[1]

    resolve(_ref("requests.get", module="requests"), kb)
    resolve(_ref("requests.post", module="requests"), kb)

    assert calls.count("requests") == 1


def test_site_packages_kb_walks_dotted_symbol_past_module():
    """`os.path.join` — module is `os.path`, leaf is `join`."""
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(
        _ref("os.path.join", module="os.path", kind="import"),
        kb,
    )
    assert res.known is True
    assert res.found is True


def test_site_packages_kb_handles_builtin_module_without_signature_fail():
    """Some builtins (e.g., numpy C funcs) don't support inspect.signature.
    That must not crash — signature may be None, but found is still True."""
    pytest.importorskip("numpy")
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(_ref("numpy.array", module="numpy"), kb)
    assert res.known is True
    assert res.found is True
    # signature may or may not be available — just don't crash.


def test_site_packages_kb_graceful_on_import_time_exception():
    """A module that raises on import → known=False, not a crash."""
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB()
    res = resolve(_ref("zzz_does_not_exist.thing", module="zzz_does_not_exist"), kb)
    assert res.known is False
    assert res.found is False
