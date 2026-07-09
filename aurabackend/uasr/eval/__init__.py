"""UASR evaluation harness — reproducible experiments proving the self-healing
layer's promises: detection, healing correctness, safety, scale, and end-to-end
error reduction. Each ``exp_*`` module is runnable standalone and writes a CSV of
raw results next to a one-line summary; ``run_all.py`` collects them into a
readiness report.
"""
