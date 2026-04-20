# Khati 2026 replication corpus

This directory is populated by running
`scripts/fetch_khati_corpus.py`. The dataset it downloads is drawn
verbatim from the authors' public replication package:

    https://github.com/WM-SEMERU/Hallucinations-in-Code
    hallucination_pipeline/data/generated_dataset.csv

Cite:

    Khati, Dipin; Rodriguez-Cardenas, Daniel; Pantzer, Paul; Poshyvanyk, Denys.
    "Detecting and Correcting Hallucinations in LLM-Generated Code via
    Deterministic AST Analysis." arXiv:2601.19106 (FORGE 2026).

Use: research validation of hallucination_ast's precision / recall
against an external baseline. Do NOT ship this file inside any
Soliton artifact — it is fetched on-demand, not vendored.

The upstream repository carries no LICENSE file at the time of fetch;
downstream redistribution is limited to local validation runs.
