"""Tests for hallucination_ast.check — the four finding rules.

Each rule tested in isolation with a hand-rolled Resolution so we don't
re-test resolve.py. The orchestration helper (check_all) is tested against
a FakeKB to verify wiring.
"""
from __future__ import annotations

import inspect

import pytest

from hallucination_ast.resolve import Resolution
from hallucination_ast.types import AstExtractedReference


def _ref(symbol, kind="method", arg_count=None, kwargs=None):
    return AstExtractedReference(
        kind=kind,
        file="a.py",
        line=3,
        column=0,
        symbol=symbol,
        module=symbol.split(".", 1)[0] if "." in symbol else symbol,
        arg_count=arg_count,
        kwargs=kwargs,
    )


def _sig(func):
    return inspect.signature(func)


# --- identifier_not_found -------------------------------------------------


def test_identifier_not_found_emitted_when_known_but_missing():
    from hallucination_ast.check import check_reference

    ref = _ref("requests.gett")
    res = Resolution(found=False, known=True, siblings=["get", "post"])

    findings = check_reference(ref, res)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "identifier_not_found"
    assert f.severity == "critical"
    assert f.confidence == 100
    assert f.symbol == "requests.gett"
    assert "gett" in f.message or "requests.gett" in f.message


def test_identifier_not_found_never_emitted_when_module_unknown():
    """Module not introspectable → silent forward to LLM, no finding here."""
    from hallucination_ast.check import check_reference

    ref = _ref("unknown_pkg.thing")
    res = Resolution(found=False, known=False)
    assert check_reference(ref, res) == []


def test_identifier_not_found_not_emitted_when_found():
    from hallucination_ast.check import check_reference

    ref = _ref("requests.get")
    res = Resolution(found=True, known=True)
    findings = check_reference(ref, res)
    assert all(f.rule != "identifier_not_found" for f in findings)


# --- signature_mismatch_arity ---------------------------------------------


def test_arity_too_few_positional():
    from hallucination_ast.check import check_reference

    def fake_get(url, params, headers): ...
    ref = _ref("requests.get", arg_count=1, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(fake_get))

    findings = check_reference(ref, res)
    assert any(f.rule == "signature_mismatch_arity" for f in findings)
    arity = next(f for f in findings if f.rule == "signature_mismatch_arity")
    assert arity.severity == "critical"
    assert arity.confidence == 100


def test_arity_too_many_positional_no_vararg():
    from hallucination_ast.check import check_reference

    def f(a, b): ...
    ref = _ref("mod.f", arg_count=3, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))

    findings = check_reference(ref, res)
    assert any(f.rule == "signature_mismatch_arity" for f in findings)


def test_arity_variadic_allows_any_count():
    from hallucination_ast.check import check_reference

    def f(*args): ...
    ref = _ref("mod.f", arg_count=5, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))

    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_arity_variadic_still_enforces_required_positional():
    """F11: `def f(a, b, *args)` — must require at least 2 positional even
    with *args present."""
    from hallucination_ast.check import check_reference

    def f(a, b, *args): ...
    ref = _ref("mod.f", arg_count=1, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert any(x.rule == "signature_mismatch_arity" for x in check_reference(ref, res))


def test_arity_variadic_accepts_more_than_required():
    """F11 partner: `def f(a, b, *args)` with arg_count=5 must pass — the
    *args sink absorbs the extras."""
    from hallucination_ast.check import check_reference

    def f(a, b, *args): ...
    ref = _ref("mod.f", arg_count=5, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_locally_bound_names_returns_empty_set_on_syntax_error():
    """F9: _locally_bound_names must absorb parse failures and return ().
    A SyntaxError propagating out would crash check_source."""
    from hallucination_ast.check import _locally_bound_names

    result = _locally_bound_names("def (broken syntax")
    assert result == set()


def test_check_source_unparseable_snippet_does_not_raise():
    """F9 partner: check_source must handle SyntaxError in input source
    without raising."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = "def (broken:\n    pass\n"
    report = check_source(src, "bad.py", SitePackagesKB())
    assert report is not None


def test_arity_skipped_when_arg_count_none():
    """Extract set arg_count=None because the call used *splat — skip check."""
    from hallucination_ast.check import check_reference

    def f(a, b): ...
    ref = _ref("mod.f", arg_count=None, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_arity_skipped_when_signature_none():
    """C-extension callable without inspectable sig — can't check arity."""
    from hallucination_ast.check import check_reference

    ref = _ref("numpy.array", arg_count=10, kwargs=[])
    res = Resolution(found=True, known=True, signature=None)
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_self_parameter_skipped_for_unbound_method_calls():
    """A method sig has `self` as first param; when accessed via a bound
    call (`instance.meth(arg)`) the receiver is passed implicitly. The
    `is_unbound_method` flag from Resolution tells _arity_bounds to strip it."""
    from hallucination_ast.check import check_reference

    class Example:
        def meth(self, url): ...

    ref = _ref("mod.meth", arg_count=1, kwargs=[])
    res = Resolution(
        found=True,
        known=True,
        signature=_sig(Example.meth),
        is_unbound_method=True,
    )
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_cls_parameter_skipped_for_classmethod_context():
    """Classmethods get `cls` as first param — also stripped when
    is_unbound_method=True (classmethods surface their cls in the sig
    only when retrieved from the class __dict__; in practice our KB
    passes is_unbound_method=False for them because the descriptor has
    already bound cls. This test pins the behavior: if an 'unbound method
    with cls' is ever passed, the heuristic catches it)."""
    from hallucination_ast.check import check_reference

    class Example:
        def create(cls, value): ...

    ref = _ref("mod.create", arg_count=1, kwargs=[])
    res = Resolution(
        found=True,
        known=True,
        signature=_sig(Example.create),
        is_unbound_method=True,
    )
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


def test_staticmethod_first_param_not_skipped():
    """Key F8 fix: for a @staticmethod whose first param is coincidentally
    named `self`, our KB resolves it with is_unbound_method=False so the
    first param is treated as a real positional. arg_count=0 must flag
    arity mismatch (was silently passed under the old unconditional
    skip)."""
    from hallucination_ast.check import check_reference

    def static_like(self, url): ...  # param coincidentally named 'self'

    ref = _ref("mod.static_like", arg_count=0, kwargs=[])
    res = Resolution(
        found=True,
        known=True,
        signature=_sig(static_like),
        is_unbound_method=False,  # staticmethod path
    )
    assert any(
        x.rule == "signature_mismatch_arity" for x in check_reference(ref, res)
    )


def test_arity_respects_defaults():
    """Params with defaults aren't required — a call that skips them is fine."""
    from hallucination_ast.check import check_reference

    def f(url, timeout=10, headers=None): ...
    ref = _ref("mod.f", arg_count=1, kwargs=[])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert all(x.rule != "signature_mismatch_arity" for x in check_reference(ref, res))


# --- signature_mismatch_keyword -------------------------------------------


def test_unknown_kwarg_emitted():
    from hallucination_ast.check import check_reference

    def f(url, timeout=None): ...
    ref = _ref("mod.f", arg_count=1, kwargs=["imeout"])
    res = Resolution(found=True, known=True, signature=_sig(f))

    findings = check_reference(ref, res)
    kw_findings = [x for x in findings if x.rule == "signature_mismatch_keyword"]
    assert len(kw_findings) == 1
    assert kw_findings[0].severity == "improvement"
    assert "imeout" in kw_findings[0].message


def test_known_kwarg_passes():
    from hallucination_ast.check import check_reference

    def f(url, timeout=None): ...
    ref = _ref("mod.f", arg_count=1, kwargs=["timeout"])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert all(x.rule != "signature_mismatch_keyword" for x in check_reference(ref, res))


def test_var_keyword_sink_accepts_any_kwarg():
    from hallucination_ast.check import check_reference

    def f(url, **extra): ...
    ref = _ref("mod.f", arg_count=1, kwargs=["anything_goes"])
    res = Resolution(found=True, known=True, signature=_sig(f))
    assert all(x.rule != "signature_mismatch_keyword" for x in check_reference(ref, res))


def test_kwarg_check_skipped_when_signature_none():
    from hallucination_ast.check import check_reference

    ref = _ref("mod.f", arg_count=1, kwargs=["anything"])
    res = Resolution(found=True, known=True, signature=None)
    assert all(x.rule != "signature_mismatch_keyword" for x in check_reference(ref, res))


# --- deprecated_identifier ------------------------------------------------


def test_deprecated_emitted_when_flag_set():
    from hallucination_ast.check import check_reference

    ref = _ref("fs.exists")
    res = Resolution(
        found=True, known=True,
        is_deprecated=True, deprecation_message="use fs.access",
    )
    findings = check_reference(ref, res)
    dep = [f for f in findings if f.rule == "deprecated_identifier"]
    assert len(dep) == 1
    assert dep[0].severity == "improvement"
    assert "fs.access" in dep[0].message


def test_deprecated_not_emitted_when_not_deprecated():
    from hallucination_ast.check import check_reference

    ref = _ref("requests.get")
    res = Resolution(found=True, known=True, is_deprecated=False)
    findings = check_reference(ref, res)
    assert all(f.rule != "deprecated_identifier" for f in findings)


# --- check_all orchestration ----------------------------------------------


def test_check_all_returns_report_with_stats(monkeypatch):
    from hallucination_ast.check import check_all
    from hallucination_ast.resolve import Resolution

    class FakeKB:
        def lookup(self, module, symbol):
            # requests.get exists, requests.gett does not.
            if symbol == "requests.gett":
                return Resolution(found=False, known=True, siblings=["get"])
            if symbol == "requests.get":
                return Resolution(found=True, known=True)
            if symbol == "unknown_mod.thing":
                return Resolution(found=False, known=False)
            if symbol == "requests":
                return Resolution(found=True, known=True)
            return Resolution(found=False, known=False)

    refs = [
        _ref("requests", kind="import"),
        _ref("requests.get", kind="method", arg_count=1, kwargs=[]),
        _ref("requests.gett", kind="method", arg_count=1, kwargs=[]),
        _ref("unknown_mod.thing", kind="method", arg_count=0, kwargs=[]),
    ]

    report = check_all(refs, FakeKB())

    assert len(report.findings) == 1
    assert report.findings[0].rule == "identifier_not_found"
    assert len(report.unresolved) == 1
    assert report.unresolved[0].symbol == "unknown_mod.thing"
    assert report.stats.total_references == 4
    assert report.stats.resolved_ok == 2   # requests + requests.get
    assert report.stats.resolved_bad == 1  # requests.gett
    assert report.stats.unresolved == 1    # unknown_mod.thing
    assert report.stats.wall_ms >= 0


def test_check_all_measures_wall_ms(monkeypatch):
    """wall_ms reflects actual time spent — not zero by default."""
    import time

    from hallucination_ast.check import check_all

    class SlowKB:
        def lookup(self, module, symbol):
            time.sleep(0.005)
            return Resolution(found=True, known=True)

    refs = [_ref(f"mod.f{i}", kind="method", arg_count=0, kwargs=[]) for i in range(3)]
    report = check_all(refs, SlowKB())
    assert report.stats.wall_ms >= 10  # 3 * 5ms = ~15ms minimum


def test_multiple_findings_per_reference_possible():
    """An identifier_not_found and a deprecated could not both fire (not-found
    implies we never resolved to a real object), but arity + kwarg CAN both
    fire on the same call. Confirm that."""
    from hallucination_ast.check import check_reference

    def f(a, b): ...  # 2 required positional, no kwargs
    ref = _ref("mod.f", arg_count=1, kwargs=["nope"])
    res = Resolution(found=True, known=True, signature=_sig(f))
    findings = check_reference(ref, res)
    rules = {x.rule for x in findings}
    assert "signature_mismatch_arity" in rules
    assert "signature_mismatch_keyword" in rules


# --- cross-cutting invariants --------------------------------------------


def test_all_findings_have_confidence_100():
    """Spec: zero-LLM, deterministic — confidence always 100."""
    from hallucination_ast.check import check_reference

    def f(a): ...
    # Trigger each rule class.
    cases = [
        (_ref("m.x"), Resolution(found=False, known=True, siblings=[])),
        (_ref("m.f", arg_count=0), Resolution(found=True, known=True, signature=_sig(f))),
        (_ref("m.f", arg_count=1, kwargs=["z"]),
         Resolution(found=True, known=True, signature=_sig(f))),
        (_ref("m.x"), Resolution(found=True, known=True, is_deprecated=True)),
    ]
    for ref, res in cases:
        for finding in check_reference(ref, res):
            assert finding.confidence == 100, finding
