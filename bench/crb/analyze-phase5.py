#!/usr/bin/env python3
"""Compute Phase 5 headline + per-language + per-agent numbers.

Run AFTER `bench/crb/run-phase5-pipeline.sh` populates
code-review-benchmark/offline/results/azure_gpt-5.2/evaluations.json.

Usage:
  python3 bench/crb/analyze-phase5.py
"""
from pathlib import Path
import json, re, sys

REPO = Path(__file__).resolve().parents[2]
CRB = REPO.parent / 'code-review-benchmark' / 'offline'
EV_FILE = CRB / 'results' / 'azure_gpt-5.2' / 'evaluations.json'
CAND_FILE = CRB / 'results' / 'azure_gpt-5.2' / 'candidates.json'
REVIEWS = REPO / 'bench' / 'crb' / 'phase5-reviews'

# Phase 3.5 baseline (from RESULTS.md § Phase 3.5)
P35 = {'tp': 77, 'fp': 343, 'fn': 59, 'goldens': 136, 'f1': 0.277,
       'p': 0.183, 'r': 0.566, 'cand_per_pr': 8.4}
P35_LANG = {
    'Java':   {'f1': 0.283, 'p': 0.191, 'r': 0.542},
    'Python': {'f1': 0.237, 'p': 0.161, 'r': 0.452},
    'Go':     {'f1': 0.326, 'p': 0.219, 'r': 0.636},
    'TS':     {'f1': 0.266, 'p': 0.170, 'r': 0.613},
    'Ruby':   {'f1': 0.291, 'p': 0.191, 'r': 0.607},
}

STOPWORDS = set(('a an the is are was were be been being have has had do does did '
                 'can could should would will of in on at to for by with from this '
                 'that these those it and or but if not so as than then when while '
                 'where which who whom whose').split())

def tokenize(text):
    toks = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}|`[^`]+`', text.lower())
    return {t.strip('`') for t in toks if len(t) > 2 and t.strip('`') not in STOPWORDS}

def parse_review(md_path):
    if not md_path.exists(): return []
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
        title = rest[:fm.start()].strip() if fm else re.sub(r' \(confidence:.*$','',rest).strip()
        body = txt[m.end():ms[i+1].start() if i+1<len(ms) else len(txt)][:2500]
        out.append({'category': m.group('cat'), 'title': title, 'file': file_,
                    'tokens': tokenize(f"{title} {file_} {body}"[:3000])})
    return out

LANG_BY_REPO = {
    'keycloak': 'Java', 'sentry': 'Python', 'grafana': 'Go',
    'discourse-graphite': 'Ruby', 'cal.com': 'TS',
    'sentry-greptile': 'Python', 'keycloak-greptile': 'Java',
}

def slug_from_url(url):
    parts = url.rstrip('/').split('/')
    repo, num = parts[-3], parts[-1]
    lm = {'sentry':'python-sentry','keycloak':'java-keycloak','grafana':'go-grafana',
          'discourse-graphite':'ruby-discourse-graphite','cal.com':'ts-calcom',
          'sentry-greptile':'python-sentry-greptile','keycloak-greptile':'java-keycloak-greptile'}
    return f"{lm.get(repo, repo)}-{num}"

def match_finding(cand_toks, findings):
    best, bs = None, 0.0
    for f in findings:
        if not f['tokens'] or not cand_toks: continue
        inter = cand_toks & f['tokens']
        score = len(inter) / len(cand_toks | f['tokens'])
        if f['file']:
            fb = f['file'].rsplit('/',1)[-1].rsplit('.',1)[0].lower()
            if len(fb) > 3 and fb in cand_toks: score += 0.15
        if score > bs: bs, best = score, f
    return best, bs

def main():
    if not EV_FILE.exists():
        sys.exit(f'Error: {EV_FILE} not found. Run bench/crb/run-phase5-pipeline.sh first.')
    if not REVIEWS.exists():
        sys.exit(f'Error: {REVIEWS} not found.')

    ev = json.load(EV_FILE.open())
    cand = json.load(CAND_FILE.open())

    # Aggregate
    total_tp = total_fp = total_fn = total_goldens = 0
    by_lang = {}
    agent_tp = {}; agent_fp = {}
    total_cands = 0; total_reviewed = 0
    crit_recall_num = crit_recall_den = 0
    severity_breakdown = {'Critical': [0,0], 'High': [0,0], 'Medium': [0,0], 'Low': [0,0]}

    for pr_url, tools in ev.items():
        if 'soliton' not in tools: continue
        s = tools['soliton']
        tp, fp, fn = s.get('tp', 0), s.get('fp', 0), s.get('fn', 0)
        total_tp += tp; total_fp += fp; total_fn += fn
        total_goldens += s.get('total_golden', 0)
        total_cands += s.get('total_candidates', 0)
        total_reviewed += 1
        # Per-language
        repo = pr_url.split('/')[-3]
        lang = LANG_BY_REPO.get(repo, 'Other')
        bl = by_lang.setdefault(lang, {'tp': 0, 'fp': 0, 'fn': 0, 'n': 0})
        bl['tp'] += tp; bl['fp'] += fp; bl['fn'] += fn; bl['n'] += 1
        # Severity
        for t in s.get('true_positives', []):
            sev = t.get('severity', 'Unknown')
            if sev in severity_breakdown: severity_breakdown[sev][0] += 1
        for fn_entry in s.get('false_negatives', []):
            sev = fn_entry.get('severity', 'Unknown')
            if sev in severity_breakdown: severity_breakdown[sev][1] += 1
        # Per-agent (via fuzzy-match back to phase5-reviews)
        slug = slug_from_url(pr_url)
        md = REVIEWS / f"{slug}.md"
        findings = parse_review(md)
        if not findings: continue
        tp_texts = {tp['matched_candidate'] for tp in s.get('true_positives', [])}
        fp_texts = {fp['candidate'] for fp in s.get('false_positives', [])}
        for c in cand.get(pr_url, {}).get('soliton', []):
            ct = c.get('text', '')
            toks = tokenize(ct)
            best, score = match_finding(toks, findings)
            cat = best['category'] if (best and score >= 0.08) else 'UNMATCHED'
            if ct in tp_texts: agent_tp[cat] = agent_tp.get(cat, 0) + 1
            elif ct in fp_texts: agent_fp[cat] = agent_fp.get(cat, 0) + 1

    # Headline
    p = total_tp/(total_tp+total_fp) if total_tp+total_fp else 0
    r = total_tp/(total_tp+total_fn) if total_tp+total_fn else 0
    f1 = 2*p*r/(p+r) if p+r else 0
    cand_per_pr = total_cands / total_reviewed if total_reviewed else 0

    print('=' * 62)
    print('Phase 5 headline (agent-dispatch change)')
    print('=' * 62)
    print(f'  n = {total_reviewed} PRs')
    print(f'  candidates       : {total_cands} (mean {cand_per_pr:.1f}/PR vs P3.5 {P35["cand_per_pr"]})')
    print(f'  TP = {total_tp:3d} | FP = {total_fp:3d} | FN = {total_fn:3d}')
    print(f'  Precision        : {p:.3f}  (P3.5: {P35["p"]:.3f}, d {p-P35["p"]:+.3f})')
    print(f'  Recall           : {r:.3f}  (P3.5: {P35["r"]:.3f}, d {r-P35["r"]:+.3f})')
    print(f'  F1               : {f1:.3f}  (P3.5: {P35["f1"]:.3f}, d {f1-P35["f1"]:+.3f})')
    print()
    print('Ship criteria verdict (from bench/crb/AUDIT_10PR.md §Appendix A):')
    if f1 >= 0.30 and r >= 0.52:
        verdict = '✅ SHIP candidate (pending per-language check below)'
    elif 0.28 <= f1 <= 0.30:
        verdict = '⚠️  HOLD (marginal)'
    else:
        verdict = '❌ CLOSE (F1 below 0.28)'
    print(f'  verdict: {verdict}')
    print()
    print('Per-language breakdown')
    print(f'  {"lang":8s} {"n":>3s} {"TP":>4s} {"FP":>4s} {"FN":>4s} {"P":>6s} {"R":>6s} {"F1":>6s} {"P35 F1":>7s} {"dF1":>7s}')
    any_lang_reg = False
    for lang in ['Java', 'Python', 'Go', 'TS', 'Ruby']:
        b = by_lang.get(lang, {'tp': 0, 'fp': 0, 'fn': 0, 'n': 0})
        lp = b['tp']/(b['tp']+b['fp']) if b['tp']+b['fp'] else 0
        lr = b['tp']/(b['tp']+b['fn']) if b['tp']+b['fn'] else 0
        lf1 = 2*lp*lr/(lp+lr) if lp+lr else 0
        p35_f1 = P35_LANG.get(lang, {}).get('f1', 0)
        dlf1 = lf1 - p35_f1
        if dlf1 < -0.03: any_lang_reg = True
        mark = '⚠️' if dlf1 < -0.03 else ('✓' if dlf1 > 0.01 else ' ')
        print(f'  {lang:8s} {b["n"]:>3d} {b["tp"]:>4d} {b["fp"]:>4d} {b["fn"]:>4d} {lp:>6.3f} {lr:>6.3f} {lf1:>6.3f} {p35_f1:>7.3f} {dlf1:>+7.3f} {mark}')
    if any_lang_reg:
        print('  ⚠️  Per-language regression > 0.03 detected — may block SHIP.')

    # Severity recall
    print()
    print('Severity-stratified recall')
    for sev in ['Critical', 'High', 'Medium', 'Low']:
        tp_s, fn_s = severity_breakdown[sev]
        tot = tp_s + fn_s
        rec = tp_s / tot if tot else 0
        print(f'  {sev:8s} : {tp_s}/{tot} = {rec:.3f}')

    # Per-agent
    print()
    print('Per-agent attribution (fuzzy-match)')
    print(f'  {"agent":22s} {"TP":>4s} {"FP":>4s} {"P":>6s}')
    all_cats = sorted(set(agent_tp)|set(agent_fp), key=lambda c: -(agent_tp.get(c,0)+agent_fp.get(c,0)))
    for c in all_cats:
        tp_c = agent_tp.get(c, 0); fp_c = agent_fp.get(c, 0)
        pc = tp_c/(tp_c+fp_c) if tp_c+fp_c else 0
        print(f'  {c:22s} {tp_c:>4d} {fp_c:>4d} {pc:>6.3f}')

    # Note — testing + consistency should be ZERO after Phase 5 change
    testing_total = agent_tp.get('testing', 0) + agent_fp.get('testing', 0)
    consistency_total = agent_tp.get('consistency', 0) + agent_fp.get('consistency', 0)
    print()
    if testing_total + consistency_total == 0:
        print('  ✓ Mechanism verified: zero testing + consistency emissions')
    else:
        print(f'  ⚠️  Unexpected: {testing_total} testing + {consistency_total} consistency findings still present')


if __name__ == '__main__':
    main()
