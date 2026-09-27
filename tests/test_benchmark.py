from agent_policy_kit.benchmark import load_cases, run_benchmark


def test_cases_file_loads():
    cases = load_cases()
    assert len(cases) >= 40
    for case in cases:
        assert case["expected"] in ("allow", "deny", "approve")
        assert isinstance(case["action"], dict)
        assert case["note"]


def test_benchmark_passes_with_full_marks():
    metrics = run_benchmark(verbose=False)
    assert metrics["accuracy"] == 1.0
    assert metrics["failures"] == []
    for decision in ("allow", "deny", "approve"):
        assert metrics[decision]["f1"] == 1.0
        assert metrics[decision]["support"] > 0
    assert metrics["latency_us"]["mean"] > 0
