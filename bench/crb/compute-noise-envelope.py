#!/usr/bin/env python3
"""Compute judge-noise envelope across N independent re-runs of the
Phase 5.2 judge pipeline on the same phase5_2-reviews/ corpus.

Reads `bench/crb/judge-noise-runs/run{N}/evaluations.json` for each run dir
present, then reports σ for:
- aggregate F1 / P / R / TP / FP / FN
- per-language F1 (TS, Python, Ruby, Go, Java; n=10 each)
- per-language max swing across runs
- per-agent TP / FP via fuzzy-match back to phase5_2-reviews/

Output: text table to stdout + machine-readable JSON to
`bench/crb/judge-noise-runs/summary.json` for the writeup.

Usage:
  PYTHONUTF8=1 python3 bench/crb/compute-noise-envelope.py
"""
from pathlib import Path
import json
import math
import re
import sys

REPO = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO / 'bench' / 'crb' / 'judge-noise-runs'
REVIEWS = REPO / 'bench' / 'crb' / 'phase5_2-reviews'

LANG_BY_REPO = {
    'keycloak': 'Java', 'sentry': 'Python', 'grafana': 'Go',
    'discourse-graphite': 'Ruby', 'cal.com': 'TS',
    'sentry-greptile': 'Python', 'keycloak-greptile': 'Java',
}
LANGS = ['Java', 'Python', 'Go', 'TS', 'Ruby']

STOPWORDS = set(('a an the is are was were be been being have has had do does did '
                 'can could should would will of in on at to for by with from this '
                 'that these those it and or but if not so as than then when while '
                 'where which who whom whose').split())


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def f1_from(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * p * r / (p + r) if p + r else 0
    return p, r, f1


def tokenize(text):
    toks = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}|`[^`]+`', text.lower())
    return {t.strip('`') for t in toks if len(t) > 2 and t.strip('`') not in STOPWORDS}


def parse_review(md_path):
    if not md_path.exists():
        return []
    txt = md_path.read_text(encoding='utf-8', errors='ignore')
    hdr = re.compile(r'^(:red_circle:|:yellow_circle:|:small_blue_diamond:) '
                     r'\[(?P<cat>[^\]]+)\] (?P<rest>.+)$', re.MULTILINE)
    fp = re.compile(r' in `?([^\s`]+?)(?::(\d+(?:-\d+)?))?`?(?: \(confidence:.*)?$')
    out = []
    ms = list(hdr.finditer(txt))
    for i, m in enumerate(ms):
        rest = m.group('rest').strip()
        fm = fp.search(rest)
        file_ = fm.group(1) if fm else ''
        title = rest[:fm.start()].strip() if fm else re.sub(r' \(confidence:.*$', '', rest).strip()
        body = txt[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(txt)][:2500]
        out.append({'category': m.group('cat'), 'title': title, 'file': file_,
                    'tokens': tokenize(f"{title} {file_} {body}"[:3000])})
    return out


def slug_from_url(url):
    parts = url.rstrip('/').split('/')
    repo, num = parts[-3], parts[-1]
    lm = {'sentry': 'python-sentry', 'keycloak': 'java-keycloak', 'grafana': 'go-grafana',
          'discourse-graphite': 'ruby-discourse-graphite', 'cal.com': 'ts-calcom',
          'sentry-greptile': 'python-sentry-greptile', 'keycloak-greptile': 'java-keycloak-greptile'}
    return f"{lm.get(repo, repo)}-{num}"


def match_finding(cand_toks, findings):
    best, bs = None, 0.0
    for f in findings:
        if not f['tokens'] or not cand_toks:
            continue
        inter = cand_toks & f['tokens']
        score = len(inter) / len(cand_toks | f['tokens'])
        if f['file']:
            fb = f['file'].rsplit('/', 1)[-1].rsplit('.', 1)[0].lower()
            if len(fb) > 3 and fb in cand_toks:
                score += 0.15
        if score > bs:
            bs, best = score, f
    return best, bs


def analyze_run(run_dir):
    """Return dict with aggregate, per-language, per-agent stats for one run."""
    ev_path = run_dir / 'evaluations.json'
    if not ev_path.exists():
        return None
    ev = json.load(ev_path.open(encoding='utf-8'))
    cand_path = run_dir / 'candidates.json'
    cand = json.load(cand_path.open(encoding='utf-8')) if cand_path.exists() else {}

    total = {'tp': 0, 'fp': 0, 'fn': 0, 'goldens': 0, 'cands': 0, 'n': 0}
    by_lang = {l: {'tp': 0, 'fp': 0, 'fn': 0, 'n': 0} for l in LANGS}
    agent_tp = {}
    agent_fp = {}

    for pr_url, tools in ev.items():
        if 'soliton' not in tools:
            continue
        s = tools['soliton']
        tp, fp, fn = s.get('tp', 0), s.get('fp', 0), s.get('fn', 0)
        total['tp'] += tp
        total['fp'] += fp
        total['fn'] += fn
        total['goldens'] += s.get('total_golden', 0)
        total['cands'] += s.get('total_candidates', 0)
        total['n'] += 1

        repo = pr_url.split('/')[-3]
        lang = LANG_BY_REPO.get(repo)
        if lang in by_lang:
            by_lang[lang]['tp'] += tp
            by_lang[lang]['fp'] += fp
            by_lang[lang]['fn'] += fn
            by_lang[lang]['n'] += 1

        slug = slug_from_url(pr_url)
        md = REVIEWS / f"{slug}.md"
        findings = parse_review(md)
        if not findings:
            continue
        tp_texts = {tp_['matched_candidate'] for tp_ in s.get('true_positives', [])}
        fp_texts = {fp_['candidate'] for fp_ in s.get('false_positives', [])}
        for c in cand.get(pr_url, {}).get('soliton', []):
            ct = c.get('text', '')
            toks = tokenize(ct)
            best, score = match_finding(toks, findings)
            cat = best['category'] if (best and score >= 0.08) else 'UNMATCHED'
            if ct in tp_texts:
                agent_tp[cat] = agent_tp.get(cat, 0) + 1
            elif ct in fp_texts:
                agent_fp[cat] = agent_fp.get(cat, 0) + 1

    p, r, f1 = f1_from(total['tp'], total['fp'], total['fn'])
    lang_f1 = {}
    for l, b in by_lang.items():
        lp, lr, lf = f1_from(b['tp'], b['fp'], b['fn'])
        lang_f1[l] = {'tp': b['tp'], 'fp': b['fp'], 'fn': b['fn'],
                      'p': lp, 'r': lr, 'f1': lf, 'n': b['n']}

    return {
        'aggregate': {'tp': total['tp'], 'fp': total['fp'], 'fn': total['fn'],
                      'p': p, 'r': r, 'f1': f1, 'cands': total['cands'], 'n': total['n']},
        'lang': lang_f1,
        'agent_tp': agent_tp,
        'agent_fp': agent_fp,
    }


def fmt(x, n=3):
    return f'{x:.{n}f}' if isinstance(x, float) else str(x)


def main():
    if not RUNS_DIR.exists():
        sys.exit(f'Error: {RUNS_DIR} not found.')

    run_dirs = sorted([d for d in RUNS_DIR.iterdir() if d.is_dir() and d.name.startswith('run')],
                      key=lambda p: int(p.name[3:]) if p.name[3:].isdigit() else -1)
    runs = []
    for d in run_dirs:
        r = analyze_run(d)
        if r is not None:
            r['name'] = d.name
            runs.append(r)

    if len(runs) < 2:
        sys.exit(f'Error: need >= 2 runs, found {len(runs)}.')

    # Aggregate noise envelope
    f1s = [r['aggregate']['f1'] for r in runs]
    ps = [r['aggregate']['p'] for r in runs]
    rs = [r['aggregate']['r'] for r in runs]
    tps = [r['aggregate']['tp'] for r in runs]
    fps = [r['aggregate']['fp'] for r in runs]
    fns = [r['aggregate']['fn'] for r in runs]

    print('=' * 78)
    print(f'Judge-noise envelope across {len(runs)} runs')
    print(f'  corpus: phase5_2-reviews/ (50 PRs, identical inputs)')
    print(f'  judge:  Azure OpenAI gpt-5.2 (managed identity)')
    print('=' * 78)
    print()
    print('Aggregate (per-run)')
    print(f'  {"run":6s} {"TP":>4s} {"FP":>4s} {"FN":>4s} {"P":>6s} {"R":>6s} {"F1":>6s}')
    for r in runs:
        a = r['aggregate']
        print(f'  {r["name"]:6s} {a["tp"]:>4d} {a["fp"]:>4d} {a["fn"]:>4d} '
              f'{a["p"]:>6.3f} {a["r"]:>6.3f} {a["f1"]:>6.3f}')
    print()
    print(f'  σ_F1 aggregate     = {stdev(f1s):.4f}  (mean {mean(f1s):.3f}; '
          f'min {min(f1s):.3f}, max {max(f1s):.3f}, range {max(f1s)-min(f1s):.4f})')
    print(f'  σ_P aggregate      = {stdev(ps):.4f}')
    print(f'  σ_R aggregate      = {stdev(rs):.4f}')
    print(f'  σ_TP aggregate     = {stdev(tps):.2f} (mean {mean(tps):.1f})')
    print(f'  σ_FP aggregate     = {stdev(fps):.2f} (mean {mean(fps):.1f})')
    print(f'  σ_FN aggregate     = {stdev(fns):.2f} (mean {mean(fns):.1f})')
    print()

    print('Per-language F1 (n=10 each)')
    print(f'  {"lang":8s} ' + ' '.join(f'{r["name"]:>7s}' for r in runs)
          + f' {"mean":>7s} {"σ":>7s} {"swing":>7s}')
    lang_summary = {}
    for l in LANGS:
        per_run = [r['lang'][l]['f1'] for r in runs]
        m = mean(per_run)
        s = stdev(per_run)
        swing = max(per_run) - min(per_run)
        lang_summary[l] = {'per_run': per_run, 'mean': m, 'sigma': s, 'swing': swing}
        cells = ' '.join(f'{x:>7.3f}' for x in per_run)
        print(f'  {l:8s} {cells} {m:>7.3f} {s:>7.4f} {swing:>7.4f}')
    print()
    sigmas = [v['sigma'] for v in lang_summary.values()]
    swings = [v['swing'] for v in lang_summary.values()]
    print(f'  σ_F1 per-language (max across langs): {max(sigmas):.4f}')
    print(f'  max per-language swing across runs:   {max(swings):.4f}')
    print()

    # Per-agent attribution noise
    all_cats = set()
    for r in runs:
        all_cats.update(r['agent_tp'])
        all_cats.update(r['agent_fp'])
    all_cats = sorted(all_cats, key=lambda c: -sum(r['agent_tp'].get(c, 0) + r['agent_fp'].get(c, 0) for r in runs))
    print('Per-agent TP/FP attribution (across runs)')
    print(f'  {"agent":22s} ' + ' '.join(f'{r["name"][3:]:>11s}' for r in runs) + f' {"σ_TP":>6s} {"σ_FP":>6s}')
    agent_summary = {}
    for c in all_cats:
        tps_c = [r['agent_tp'].get(c, 0) for r in runs]
        fps_c = [r['agent_fp'].get(c, 0) for r in runs]
        s_tp, s_fp = stdev(tps_c), stdev(fps_c)
        agent_summary[c] = {'tp_per_run': tps_c, 'fp_per_run': fps_c,
                            'sigma_tp': s_tp, 'sigma_fp': s_fp}
        cells = ' '.join(f'{tp_c:>3d}/{fp_c:>3d}  ' for tp_c, fp_c in zip(tps_c, fps_c))
        print(f'  {c:22s} {cells}{s_tp:>6.2f} {s_fp:>6.2f}')
    print()

    # Retroactive calibration: prior phase deltas
    sigma_agg = stdev(f1s)
    PRIOR = [
        ('Phase 4c   vs P3.5', 0.261 - 0.277),
        ('Phase 4c.1 vs P3.5', 0.278 - 0.277),
        ('Phase 3.5.1 vs P3.5', 0.243 - 0.277),
        ('Phase 5    vs P3.5', 0.300 - 0.277),
        ('Phase 5.2  vs P3.5', 0.313 - 0.277),
        ('Phase 5.2  vs P5', 0.313 - 0.300),
        ('Phase 5.2.1 vs P5.2', 0.308 - 0.313),
    ]
    print('Retroactive calibration vs measured σ (1σ / 2σ thresholds)')
    print(f'  σ_F1 aggregate = {sigma_agg:.4f}; 1σ = {sigma_agg:.4f}, 2σ = {2*sigma_agg:.4f}')
    print(f'  {"comparison":24s} {"Δ F1":>8s} {"|Δ|/σ":>7s} {"verdict":>15s}')
    for label, delta in PRIOR:
        if sigma_agg == 0:
            verdict = 'σ=0 (skip)'
        else:
            ratio = abs(delta) / sigma_agg
            if ratio >= 2:
                verdict = '> 2σ (signal)'
            elif ratio >= 1:
                verdict = '1-2σ (provisional)'
            else:
                verdict = '< 1σ (noise)'
            ratio_str = f'{ratio:.2f}'
        print(f'  {label:24s} {delta:>+8.4f} {ratio_str:>7s} {verdict:>15s}')

    # Write JSON
    out_path = RUNS_DIR / 'summary.json'
    summary = {
        'n_runs': len(runs),
        'runs': [{'name': r['name'], 'aggregate': r['aggregate'],
                  'lang': r['lang']} for r in runs],
        'sigma_aggregate': {
            'f1': stdev(f1s), 'p': stdev(ps), 'r': stdev(rs),
            'tp': stdev(tps), 'fp': stdev(fps), 'fn': stdev(fns),
            'mean_f1': mean(f1s), 'min_f1': min(f1s), 'max_f1': max(f1s),
        },
        'per_language': {l: lang_summary[l] for l in LANGS},
        'per_agent': agent_summary,
        'retroactive_calibration': [{'comparison': lab, 'delta': d,
                                     'ratio_to_sigma': abs(d) / sigma_agg if sigma_agg else None}
                                    for lab, d in PRIOR],
    }
    out_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print()
    print(f'  → wrote {out_path}')


if __name__ == '__main__':
    main()
