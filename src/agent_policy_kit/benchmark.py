"""Benchmark runner: labeled action scenarios for the example policies.

Run it yourself:

    python -m agent_policy_kit.benchmark

The engine is deterministic, so this is a wiring check, not a machine
learning evaluation: it proves the loader, the matchers, the rule
ordering, and the time windows all behave the way the README table
claims. Every case lives in benchmarks/cases.jsonl with a note saying
why the expected decision is right. Add cases there when you add
features.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_policy_kit import evaluate, load_policy

HERE = Path(__file__).resolve().parent.parent.parent
POLICY_DIR = HERE / "examples" / "policies"
CASES_FILE = HERE / "benchmarks" / "cases.jsonl"

DECISIONS = ("allow", "deny", "approve")


def load_cases() -> list[dict]:
    cases = []
    for line in CASES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def run_benchmark(verbose: bool = True) -> dict:
    policies = {}
    for path in sorted(POLICY_DIR.glob("*.yaml")):
        policy = load_policy(path)
        policies[policy.name] = policy

    cases = load_cases()
    latencies: list[float] = []
    confusion: dict[str, dict[str, int]] = {d: {e: 0 for e in DECISIONS} for d in DECISIONS}
    failures: list[str] = []

    for case in cases:
        policy = policies[case["policy"]]
        now = None
        if case.get("now"):
            now = datetime.fromisoformat(case["now"])
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
        started = time.perf_counter()
        decision = evaluate(policy, case["action"], now=now)
        latencies.append((time.perf_counter() - started) * 1e6)
        confusion[case["expected"]][decision.decision] += 1
        if decision.decision != case["expected"]:
            failures.append(
                f"{case['id']}: expected {case['expected']}, "
                f"got {decision.decision} ({decision.reason})"
            )

    total = len(cases)
    correct = sum(confusion[d][d] for d in DECISIONS)
    metrics = {"accuracy": correct / total, "total": total, "failures": failures}
    for d in DECISIONS:
        tp = confusion[d][d]
        fp = sum(confusion[e][d] for e in DECISIONS if e != d)
        fn = sum(confusion[d][e] for e in DECISIONS if e != d)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics[d] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(confusion[d].values()),
        }
    latencies.sort()
    metrics["latency_us"] = {
        "mean": sum(latencies) / len(latencies),
        "p50": latencies[len(latencies) // 2],
        "p99": latencies[int(len(latencies) * 0.99)],
    }

    if verbose:
        print(f"cases: {total}, accuracy: {metrics['accuracy']:.2f} ({correct}/{total})")
        print()
        print(f"{'decision':8} {'precision':>9} {'recall':>7} {'f1':>6}  support")
        for d in DECISIONS:
            m = metrics[d]
            print(f"{d:8} {m['precision']:9.2f} {m['recall']:7.2f} {m['f1']:6.2f}  {m['support']}")
        lat = metrics["latency_us"]
        print()
        print(
            f"eval latency: mean {lat['mean']:.1f} us, "
            f"p50 {lat['p50']:.1f} us, p99 {lat['p99']:.1f} us"
        )
        if failures:
            print()
            print("FAILURES:")
            for f in failures:
                print("  " + f)

    if failures:
        raise SystemExit(f"benchmark failed: {len(failures)} case(s) mismatched")
    return metrics


if __name__ == "__main__":
    run_benchmark()
