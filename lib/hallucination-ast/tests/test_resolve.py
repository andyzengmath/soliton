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


# --- Allowlist for arbitrary-import RCE defense (PR #26 security review) ----


def test_site_packages_kb_refuses_non_allowlisted_root_without_import(monkeypatch):
    """Untrusted module root not in stdlib + allowlist and not pre-imported:
    must NOT call importlib.import_module (could execute attacker code).

    Spies on importlib.import_module itself to prove the outer entry was
    never reached — the allowlist check inside _import_module raises first."""
    import importlib
    import sys

    from hallucination_ast.resolve import SitePackagesKB, resolve

    fake_name = "attacker_controlled_pkg_xyz_123"
    assert fake_name not in sys.modules

    real = importlib.import_module
    calls: list[str] = []

    def _spy(name, *a, **kw):
        calls.append(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", _spy)

    kb = SitePackagesKB()
    res = resolve(_ref(f"{fake_name}.thing", module=fake_name), kb)

    # Must forward (known=False), NOT execute the import.
    assert res.known is False
    assert res.found is False
    assert fake_name not in calls, (
        f"allowlist bypass: importlib.import_module({fake_name!r}) was called"
    )


def test_site_packages_kb_import_module_raises_importerror_for_disallowed_root():
    """Direct unit test: _import_module raises ImportError for non-allowlisted
    roots that aren't in sys.modules."""
    import sys

    from hallucination_ast.resolve import SitePackagesKB

    fake = "attacker_controlled_pkg_xyz_456"
    assert fake not in sys.modules

    kb = SitePackagesKB()
    with pytest.raises(ImportError):
        kb._import_module(fake)


def test_get_cached_stores_sentinel_by_identity():
    """F35: on import failure, _get_cached stores exactly the singleton
    sentinel object — not a copy, not None. `is` identity must survive
    the cache round-trip."""
    from hallucination_ast.resolve import SitePackagesKB

    kb = SitePackagesKB()

    def _boom(name):
        raise ImportError("test-forced failure")

    kb._import_module = _boom
    result = kb._get_cached("fake_pkg_for_sentinel_test")
    assert result is kb._SENTINEL_MISSING

    # Second call must hit cache — no re-import attempted.
    calls: list[str] = []

    def _spy(name):
        calls.append(name)
        raise ImportError("should not reach here")

    kb._import_module = _spy
    result2 = kb._get_cached("fake_pkg_for_sentinel_test")
    assert result2 is kb._SENTINEL_MISSING
    assert calls == []


def test_lookup_sets_is_unbound_method_for_plain_class_method():
    """F8 partner at the KB level: `Class.method` resolution marks the
    result as is_unbound_method=True so _arity_bounds strips self."""
    from hallucination_ast.resolve import SitePackagesKB

    # Inject a synthetic class into a temp module to test the lookup path.
    import sys
    import types as _types

    mod = _types.ModuleType("_unbound_method_test_mod")

    class Example:
        def meth(self, url): ...

        @staticmethod
        def sm(x): ...

        @classmethod
        def cm(cls, x): ...

    mod.Example = Example
    sys.modules["_unbound_method_test_mod"] = mod
    try:
        kb = SitePackagesKB(
            allowed_packages=frozenset({"_unbound_method_test_mod"})
        )
        res_meth = kb.lookup(
            "_unbound_method_test_mod", "_unbound_method_test_mod.Example.meth"
        )
        assert res_meth.found is True
        assert res_meth.is_unbound_method is True

        res_sm = kb.lookup(
            "_unbound_method_test_mod", "_unbound_method_test_mod.Example.sm"
        )
        assert res_sm.found is True
        # Staticmethod — the descriptor in __dict__ is a staticmethod instance.
        assert res_sm.is_unbound_method is False

        res_cm = kb.lookup(
            "_unbound_method_test_mod", "_unbound_method_test_mod.Example.cm"
        )
        assert res_cm.found is True
        # Classmethod — also not treated as "unbound method" for our strip logic.
        assert res_cm.is_unbound_method is False
    finally:
        del sys.modules["_unbound_method_test_mod"]


def test_site_packages_kb_allows_stdlib_without_explicit_allowlist_entry():
    """Stdlib modules (e.g. json, urllib) must resolve without being on the
    curated allowlist — they're already safe to import by definition."""
    from hallucination_ast.resolve import SitePackagesKB, resolve

    kb = SitePackagesKB(allowed_packages=frozenset())  # empty curated list
    res = resolve(_ref("json.dumps", module="json"), kb)
    assert res.known is True
    assert res.found is True


def test_site_packages_kb_allows_pre_imported_modules():
    """A module already in sys.modules is safe to return (the import side
    effects already happened). Don't refuse pre-imported names."""
    import sys

    from hallucination_ast.resolve import SitePackagesKB, resolve

    # pytest itself is pre-imported (we're running inside it).
    assert "pytest" in sys.modules

    kb = SitePackagesKB(allowed_packages=frozenset())  # not on allowlist
    res = resolve(_ref("pytest.fixture", module="pytest"), kb)
    assert res.known is True
    # May or may not be "found" depending on whether fixture is in dir(pytest);
    # the security-relevant assertion is that we got a non-None mod (known=True).


def test_site_packages_kb_custom_allowlist_overrides_default():
    """Constructor accepts a custom allowlist."""
    from hallucination_ast.resolve import SitePackagesKB, resolve

    # Allow ONLY 'json' — nothing else, not even numpy.
    kb = SitePackagesKB(allowed_packages=frozenset({"json"}))
    res_json = resolve(_ref("json.dumps", module="json"), kb)
    assert res_json.known is True

    # numpy is in the default allowlist but NOT in this custom one. Since
    # pytest may or may not have numpy pre-imported, we can only assert
    # the weaker property: if numpy is not pre-imported, it's refused.
    import sys
    if "numpy" not in sys.modules:
        res_np = resolve(_ref("numpy.array", module="numpy"), kb)
        assert res_np.known is False
