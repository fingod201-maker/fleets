"""Compute pass thresholds from verify/attempts.csv (SPEC §9).

usage: python verify/tally.py [verify/attempts.csv]
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

THRESHOLDS = {
    "executed": 0.90,
    "no_dialog": 0.90,
    "session_60s": 0.90,
    "alive24": 0.85,
    "alive72": 0.70,
}
MIN_SAMPLE = 10
BOOL_FIELDS = ("executed", "no_dialog", "session_60s", "alive24", "alive48", "alive72")


def truthy(value: str) -> bool:
    return str(value).strip().lower() in {"y", "yes", "1", "true"}


def load(path: str) -> list[dict[str, str]]:
    from pathlib import Path

    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentages(rows: list[dict[str, str]]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        buckets[row.get("target", "?")].append(row)
    out: dict[str, dict[str, float | int]] = {}
    for target, items in buckets.items():
        n = len(items)
        counts = {field: sum(1 for r in items if truthy(r.get(field, ""))) for field in THRESHOLDS}
        metrics = {field: counts[field] / n for field in THRESHOLDS}
        metrics["n"] = n
        out[target] = metrics
    return out


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "verify/attempts.csv"
    rows = load(path)
    if not rows:
        print(f"no rows in {path}")
        return 1

    failed = 0
    for target, metrics in sorted(percentages(rows).items()):
        print(f"\n{target}  (n={metrics['n']})")
        for field, threshold in THRESHOLDS.items():
            value = float(metrics[field])
            ok = value >= threshold
            if not ok:
                failed += 1
            flag = "PASS" if ok else "FAIL"
            print(f"  {field:<12} {value * 100:5.1f}%  (>= {threshold * 100:.0f}%)  {flag}")
        if int(metrics["n"]) < MIN_SAMPLE:
            print(f"  WARN small sample ({metrics['n']} < {MIN_SAMPLE})")

    print("\nOVERALL:", "PASS" if failed == 0 else f"FAIL ({failed} metric(s))")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
