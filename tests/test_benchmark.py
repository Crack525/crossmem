"""Tests for recall benchmark suite."""

from pathlib import Path

from click.testing import CliRunner

from crossmem.benchmark import default_benchmark_cases, run_benchmark, seed_benchmark_memories
from crossmem.cli import main
from crossmem.store import MemoryStore


def test_run_benchmark_metrics_in_range(tmp_path: Path) -> None:
    store = MemoryStore(db_path=tmp_path / "bench.db")
    try:
        seed_benchmark_memories(store)
        report = run_benchmark(store, cases=default_benchmark_cases(), limit=5, expanded=True)
    finally:
        store.close()

    assert report.total_cases > 0
    assert 0.0 <= report.recall_at_k <= 1.0
    assert 0.0 <= report.precision_at_k <= 1.0
    assert 0.0 <= report.mrr <= 1.0
    assert 0.0 <= report.noise_rate <= 1.0


def test_recall_gate(tmp_path: Path) -> None:
    """CI gate: Recall@5 must stay >= 0.80 with expanded search."""
    store = MemoryStore(db_path=tmp_path / "bench.db")
    try:
        seed_benchmark_memories(store)
        report = run_benchmark(store, cases=default_benchmark_cases(), limit=5, expanded=True)
    finally:
        store.close()

    assert report.recall_at_k >= 1.00, f"Recall@5 regression: {report.recall_at_k:.2f} < 1.00"


def test_noise_gate(tmp_path: Path) -> None:
    """CI gate: Noise rate must stay <= 0.25 with expanded search."""
    store = MemoryStore(db_path=tmp_path / "bench.db")
    try:
        seed_benchmark_memories(store)
        report = run_benchmark(store, cases=default_benchmark_cases(), limit=5, expanded=True)
    finally:
        store.close()

    assert report.noise_rate <= 0.10, f"Noise regression: {report.noise_rate:.2f} > 0.10"


def test_expanded_beats_or_matches_strict_for_recall(tmp_path: Path) -> None:
    store = MemoryStore(db_path=tmp_path / "bench.db")
    try:
        seed_benchmark_memories(store)
        cases = default_benchmark_cases()
        strict_report = run_benchmark(store, cases=cases, limit=5, expanded=False)
        expanded_report = run_benchmark(store, cases=cases, limit=5, expanded=True)
    finally:
        store.close()

    assert expanded_report.recall_at_k >= strict_report.recall_at_k


def test_benchmark_cli_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["benchmark", "--limit", "5"])

    assert result.exit_code == 0
    assert "Recall benchmark suite" in result.output
    assert "Recall@5:" in result.output
    assert "Precision@5:" in result.output
    assert "Noise rate:" in result.output


def test_benchmark_cli_strict_mode_runs() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["benchmark", "--limit", "5", "--strict"])

    assert result.exit_code == 0
    assert "mode=strict" in result.output
