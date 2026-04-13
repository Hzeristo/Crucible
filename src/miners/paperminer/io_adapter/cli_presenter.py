"""CLI presentation utilities for single-paper workflow output."""

from __future__ import annotations

import sys
import textwrap

from src.miners.paperminer.core.verdict import PaperAnalysisResult


def _term_green(text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[92m{text}\033[0m"


def print_success(message: str) -> None:
    print(_term_green(message))


def print_triage_banner(result: PaperAnalysisResult) -> None:
    w = 80
    line = "═" * w
    print()
    print(f"╔{line}╗")
    print("║" + " TRIAGE RESULT".center(w) + "║")
    print(f"╚{line}╝")
    print(f"  Verdict        : {result.verdict.value}")
    print(f"  Score          : {result.score}/10")
    print(f"  Short moniker  : {result.short_moniker}")
    novelty = textwrap.fill(
        result.novelty_delta,
        width=w - 4,
        initial_indent="  ",
        subsequent_indent="    ",
    )
    print("  Novelty Δ      :")
    print(novelty)
    print("  Baselines      :")
    if result.baseline_models:
        for item in result.baseline_models:
            print(f"    • {item}")
    else:
        print("    (none)")
    print("  Datasets       :")
    if result.evaluation_datasets:
        for item in result.evaluation_datasets:
            print(f"    • {item}")
    else:
        print("    (none)")
    print("  Core steps     :")
    if result.core_algorithm_steps:
        for i, step in enumerate(result.core_algorithm_steps, 1):
            wrapped = textwrap.fill(
                step,
                width=w - 6,
                initial_indent=f"    {i}. ",
                subsequent_indent="       ",
            )
            print(wrapped)
    else:
        print("    (none)")
    if result.critical_flaws:
        print("  Critical flaws :")
        for flaw in result.critical_flaws:
            for line in textwrap.wrap(flaw, width=w - 4):
                print(f"    {line}")
    print()
