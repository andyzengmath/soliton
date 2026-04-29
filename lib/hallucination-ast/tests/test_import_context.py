"""Tests for alias rewriting + missing-import detection.

These are the two features required to hit Khati 2026 recall targets on
their 200-sample corpus. Without them, every `plt.foo()` / `np.foo()` /
`pd.foo()` reference goes unresolved (alias not a real module name) and
every missing-import hallucination slips through.
"""
from __future__ import annotations

import pytest

from hallucination_ast.resolve import Resolution


# --- ImportInfo extraction ------------------------------------------------


def test_extract_imports_info_captures_import_as_alias():
    from hallucination_ast.extract import extract_imports_info

    info = extract_imports_info("import numpy as np\n")
    assert info.alias_to_module == {"np": "numpy"}
    assert info.imported_roots == {"numpy"}


def test_extract_imports_info_captures_plain_import():
    from hallucination_ast.extract import extract_imports_info

    info = extract_imports_info("import requests\n")
    assert info.alias_to_module == {}
    assert info.imported_roots == {"requests"}


def test_extract_imports_info_captures_dotted_import_root():
    from hallucination_ast.extract import extract_imports_info

    info = extract_imports_info("import os.path\n")
    assert info.imported_roots == {"os"}


def test_extract_imports_info_captures_from_import():
    from hallucination_ast.extract import extract_imports_info

    info = extract_imports_info("from requests import get, post\n")
    # Names imported — treat as if both are locally bound.
    assert "get" in info.imported_roots or "requests" in info.imported_roots


def test_extract_imports_info_captures_from_import_as_alias():
    from hallucination_ast.extract import extract_imports_info

    info = extract_imports_info("from requests import get as http_get\n")
    # `http_get` is a locally-bound name in this file.
    assert "http_get" in info.imported_roots


def test_extract_imports_info_captures_all_five_khati_libs():
    from hallucination_ast.extract import extract_imports_info

    src = (
        "import numpy as np\n"
        "import pandas as pd\n"
        "import matplotlib.pyplot as plt\n"
        "import requests\n"
        "import json\n"
    )
    info = extract_imports_info(src)
    assert info.alias_to_module == {
        "np": "numpy",
        "pd": "pandas",
        "plt": "matplotlib.pyplot",
    }
    assert info.imported_roots >= {"numpy", "pandas", "matplotlib", "requests", "json"}


# --- check_source: alias rewriting -----------------------------------------


def test_check_source_clean_numpy_alias_no_findings():
    """Ground-truth Khati id=1: `import numpy as np; np.average(data)`."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import numpy as np\n"
        "def func(data):\n"
        "    return np.average(data)\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_source_typo_under_alias_flagged():
    """Khati id=3 hallucination: `pd.reda_csv('x.csv')`."""
    pytest.importorskip("pandas")
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import pandas as pd\n"
        "def func(y):\n"
        "    return pd.reda_csv('data.csv')\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    rules = [f.rule for f in report.findings]
    assert "identifier_not_found" in rules
    # Suggested fix should propose "read_csv".
    inv = next(f for f in report.findings if f.rule == "identifier_not_found")
    assert inv.suggested_fix == "read_csv"


def test_check_source_clean_matplotlib_alias_no_findings():
    """Khati id=2 ground_truth: `plt.plot(x, x)`."""
    pytest.importorskip("matplotlib")
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import matplotlib.pyplot as plt\n"
        "def func(x):\n"
        "    return plt.plot(x, x)\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_source_typo_matplotlib_method_flagged():
    """Khati id=2 hallucination: `plt.plotx(x)`."""
    pytest.importorskip("matplotlib")
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import matplotlib.pyplot as plt\n"
        "def func(x):\n"
        "    return plt.plotx(x)\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    assert any(f.rule == "identifier_not_found" for f in report.findings)


# --- check_source: missing-import detection -------------------------------


def test_check_source_missing_import_alias_flagged():
    """Khati id=1 hallucination: `np.average(data)` with NO `import numpy as np`."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = "def func(data):\n    return np.average(data)\n"
    report = check_source(src, "a.py", SitePackagesKB())
    assert len(report.findings) >= 1
    f = report.findings[0]
    assert f.rule == "identifier_not_found"
    assert "np" in f.symbol or "np" in f.message


def test_check_source_missing_import_real_module_flagged():
    """Khati id=7 hallucination: `requests.get(y)` with NO `import requests`."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = "def func(y):\n    return requests.get(y)\n"
    report = check_source(src, "a.py", SitePackagesKB())
    assert any(f.rule == "identifier_not_found" for f in report.findings)


def test_check_source_stdlib_module_without_import_is_flagged():
    """Khati 2026 treats `json.dumps(x)` without `import json` as a
    hallucination — the code is a guaranteed runtime NameError. Matching
    that methodology improves recall on 11 corpus samples at the cost of
    1 edge case (stdlib used via an implicit mechanism). Rationale: real
    Soliton diffs loaded with repo_root will see the import from disk
    and won't false-positive; standalone Python snippets without imports
    are genuine bugs."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = "def func(x):\n    return json.dumps(x)\n"
    report = check_source(src, "a.py", SitePackagesKB())
    assert any(
        f.rule == "identifier_not_found" and "json" in f.symbol
        for f in report.findings
    )


def test_check_source_function_parameter_not_flagged_as_missing_import():
    """`def func(df): return df.to_csv(...)` — `df` is a parameter, not
    an unbound module. Must not be flagged. This is the false-positive
    that showed up on Khati id=157 in the first gate run; fixing it
    pushes precision from 0.986 to 0.994."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import pandas as pd\n"
        "def func(df):\n"
        "    return df.to_csv('o.csv')\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_source_local_assignment_not_flagged_as_missing_import():
    """`x = pd.DataFrame(...); x.to_csv(...)` — `x` is a local assignment,
    not an unbound module."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import pandas as pd\n"
        "def func():\n"
        "    x = pd.DataFrame({'a':[1]})\n"
        "    return x.to_csv('o.csv')\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_source_doesnt_duplicate_missing_import_findings():
    """If `np.foo` and `np.bar` both appear in a file without `import numpy
    as np`, we should emit ONE missing-import finding, not two."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "def func(x):\n"
        "    a = np.array(x)\n"
        "    b = np.sum(a)\n"
        "    return b\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    missing = [f for f in report.findings if "np" in f.symbol and f.rule == "identifier_not_found"]
    assert len(missing) == 1


def test_check_source_aliased_to_nonexistent_module_forwards():
    """`import nump as np` — we can't confirm `nump` exists, but we also
    can't call it a hallucination (forwarded to LLM). No finding here."""
    from hallucination_ast.check import check_source
    from hallucination_ast.resolve import SitePackagesKB

    src = (
        "import nump as np\n"
        "def func(x):\n"
        "    return np.average(x)\n"
    )
    report = check_source(src, "a.py", SitePackagesKB())
    # Should NOT flag (precision > recall) — `nump` is unknown, forward to LLM.
    assert report.findings == [], report.findings
