"""Deterministic recall benchmark suite for search quality measurement."""

from dataclasses import dataclass

from crossmem.store import MemoryStore


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark test case."""

    case_id: str
    query: str
    expected_terms: tuple[str, ...]
    project: str | None = None
    min_relevant: int = 1


@dataclass(frozen=True)
class CaseResult:
    """Result for a single benchmark case."""

    case_id: str
    query: str
    relevant: int
    returned: int
    recall_hit: bool
    precision_at_k: float
    first_relevant_rank: int | None
    mrr_contribution: float


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregated benchmark report."""

    total_cases: int
    passed_cases: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    noise_rate: float
    case_results: list[CaseResult]


def seed_benchmark_memories(store: MemoryStore) -> None:
    """Insert deterministic memories for benchmark scoring."""
    memories = [
        (
            "Use JWT token validation middleware in FastAPI for every protected endpoint.",
            "bench.md",
            "backend-api",
            "Security",
        ),
        (
            "Rotate API credentials every 90 days and keep secrets in a secret manager.",
            "bench.md",
            "backend-api",
            "Security",
        ),
        (
            "Database migration rollback process: revert schema, restore backup, rerun checks.",
            "bench.md",
            "backend-api",
            "Database",
        ),
        (
            "Release process for PyPI: bump version, build wheel, publish package.",
            "bench.md",
            "backend-api",
            "Release",
        ),
        (
            "Docker deployment checklist for production rollout with health checks.",
            "bench.md",
            "backend-api",
            "Deployment",
        ),
        (
            "Structured logging with correlation IDs speeds up debugging.",
            "bench.md",
            "backend-api",
            "Observability",
        ),
        (
            "Never store keys in environment variables for production workloads.",
            "bench.md",
            "backend-api",
            "Security",
        ),
        (
            "Testing workflow: run unit tests, integration tests, then pre-commit hooks.",
            "bench.md",
            "backend-api",
            "Testing",
        ),
        (
            "Mobile app release checklist for app store submission and screenshots.",
            "bench.md",
            "mobile-app",
            "Release",
        ),
        (
            "Frontend auth flow caches session token and refreshes before expiry.",
            "bench.md",
            "mobile-app",
            "Auth",
        ),
    ]
    for content, source_file, project, section in memories:
        store.add(content, source_file, project, section)


def default_benchmark_cases() -> list[BenchmarkCase]:
    """Return benchmark cases that mimic real agent recall prompts."""
    return [
        BenchmarkCase(
            case_id="TC01-exact-auth",
            query="JWT token validation",
            expected_terms=("jwt", "token", "validation"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC02-synonym-credentials",
            query="validate credentials",
            expected_terms=("credentials", "secret manager", "token"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC03-paraphrase-rollback",
            # Compound-word gap: query uses "roll back" while memory uses "rollback".
            query="how to roll back migration",
            expected_terms=("migration", "rollback", "schema"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC04-release-ship",
            query="ship new package version",
            expected_terms=("pypi", "version", "publish"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC05-deploy-rollout",
            query="production deployment rollout",
            expected_terms=("docker", "deployment", "rollout"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC06-debugging",
            query="debug service failures quickly",
            expected_terms=("logging", "debugging", "correlation"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC07-policy-negation",
            query="do not keep keys in env vars",
            expected_terms=("never store keys", "environment", "production"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC08-hyphenated",
            query="pre-commit testing",
            expected_terms=("pre-commit", "testing", "unit tests"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC09-cross-project-filter",
            query="release checklist",
            expected_terms=("pypi", "publish", "version"),
            project="backend-api",
        ),
        BenchmarkCase(
            case_id="TC10-auth-paraphrase",
            query="credential rotation and auth",
            expected_terms=("credentials", "rotate", "secret manager"),
            project="backend-api",
        ),
    ]


def run_benchmark(
    store: MemoryStore,
    *,
    cases: list[BenchmarkCase],
    limit: int = 5,
    expanded: bool = True,
) -> BenchmarkReport:
    """Run recall benchmark and return aggregate metrics."""
    case_results: list[CaseResult] = []

    for case in cases:
        if expanded:
            results = store.search_expanded(case.query, project=case.project, limit=limit)
        else:
            results = store.search(case.query, project=case.project, limit=limit)

        relevant = 0
        first_relevant_rank: int | None = None
        expected = tuple(term.lower() for term in case.expected_terms)

        for rank, result in enumerate(results, 1):
            text = result.memory.content.lower()
            if any(term in text for term in expected):
                relevant += 1
                if first_relevant_rank is None:
                    first_relevant_rank = rank

        returned = len(results)
        precision = relevant / returned if returned else 0.0
        recall_hit = relevant >= case.min_relevant
        mrr_contribution = (1.0 / first_relevant_rank) if first_relevant_rank else 0.0

        case_results.append(
            CaseResult(
                case_id=case.case_id,
                query=case.query,
                relevant=relevant,
                returned=returned,
                recall_hit=recall_hit,
                precision_at_k=precision,
                first_relevant_rank=first_relevant_rank,
                mrr_contribution=mrr_contribution,
            )
        )

    total = len(case_results)
    passed = sum(1 for r in case_results if r.recall_hit)
    avg_precision = sum(r.precision_at_k for r in case_results) / total if total else 0.0
    mrr = sum(r.mrr_contribution for r in case_results) / total if total else 0.0
    recall_at_k = passed / total if total else 0.0
    noise_rate = 1.0 - avg_precision

    return BenchmarkReport(
        total_cases=total,
        passed_cases=passed,
        recall_at_k=recall_at_k,
        precision_at_k=avg_precision,
        mrr=mrr,
        noise_rate=noise_rate,
        case_results=case_results,
    )
