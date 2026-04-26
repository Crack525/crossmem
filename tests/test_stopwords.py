"""Tests for crossmem.stopwords — two-layer token noise filter."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from crossmem.stopwords import (
    ARTICLES,
    AUXILIARIES,
    CLOSED_CLASS,
    CONJUNCTIONS,
    CONVERSATIONAL_FILLER,
    DETERMINERS,
    PREPOSITIONS,
    PRONOUNS,
    is_noise_token,
    partition_query,
)
from crossmem.store import MemoryStore


# ---------------------------------------------------------------------------
# Static set integrity
# ---------------------------------------------------------------------------


class TestClosedClassConstants:
    def test_articles_subset_of_closed_class(self) -> None:
        assert ARTICLES <= CLOSED_CLASS

    def test_prepositions_subset_of_closed_class(self) -> None:
        assert PREPOSITIONS <= CLOSED_CLASS

    def test_pronouns_subset_of_closed_class(self) -> None:
        assert PRONOUNS <= CLOSED_CLASS

    def test_conjunctions_subset_of_closed_class(self) -> None:
        assert CONJUNCTIONS <= CLOSED_CLASS

    def test_auxiliaries_subset_of_closed_class(self) -> None:
        assert AUXILIARIES <= CLOSED_CLASS

    def test_determiners_subset_of_closed_class(self) -> None:
        assert DETERMINERS <= CLOSED_CLASS

    def test_closed_class_size_reasonable(self) -> None:
        # Should have ~160-180 words across 6 categories
        assert 150 <= len(CLOSED_CLASS) <= 200

    def test_closed_class_is_frozenset(self) -> None:
        assert isinstance(CLOSED_CLASS, frozenset)

    def test_conversational_filler_is_frozenset(self) -> None:
        assert isinstance(CONVERSATIONAL_FILLER, frozenset)

    def test_conversational_filler_no_overlap_required(self) -> None:
        # Filler and closed-class can overlap — that's fine (lookup is union anyway)
        # Just assert it has some content
        assert len(CONVERSATIONAL_FILLER) >= 20

    def test_key_prepositions_present(self) -> None:
        for word in ("up", "in", "for", "to", "of", "by", "with", "from", "at"):
            assert word in CLOSED_CLASS, f"Preposition '{word}' missing from CLOSED_CLASS"

    def test_key_auxiliaries_present(self) -> None:
        for word in ("is", "are", "was", "do", "does", "did", "have", "has", "will", "can"):
            assert word in CLOSED_CLASS, f"Auxiliary '{word}' missing from CLOSED_CLASS"

    def test_key_pronouns_present(self) -> None:
        for word in ("i", "we", "you", "they", "it", "this", "that"):
            assert word in CLOSED_CLASS, f"Pronoun '{word}' missing from CLOSED_CLASS"

    def test_domain_terms_not_in_closed_class(self) -> None:
        domain_terms = [
            "jwt", "docker", "rollback", "migration", "credential",
            "deploy", "release", "auth", "testing", "logging",
            "oauth", "kubernetes", "terraform", "redis",
        ]
        for term in domain_terms:
            assert term not in CLOSED_CLASS, (
                f"Domain term '{term}' incorrectly in CLOSED_CLASS"
            )

    def test_conversational_filler_contains_key_words(self) -> None:
        for word in ("yes", "ok", "please", "thanks", "just", "really"):
            assert word in CONVERSATIONAL_FILLER, (
                f"Expected filler word '{word}' missing from CONVERSATIONAL_FILLER"
            )


# ---------------------------------------------------------------------------
# is_noise_token — Layer 1 only (no DB needed)
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_db() -> sqlite3.Connection:
    """In-memory SQLite with empty memories_fts FTS5 table."""
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE VIRTUAL TABLE memories_fts USING fts5("
        "content, project, section, keywords, "
        "tokenize='porter unicode61')"
    )
    db.commit()
    return db


class TestIsNoiseToken:
    def test_closed_class_token_is_noise_without_db(self, empty_db: sqlite3.Connection) -> None:
        assert is_noise_token("up", empty_db, 0) is True
        assert is_noise_token("in", empty_db, 0) is True
        assert is_noise_token("the", empty_db, 0) is True
        assert is_noise_token("is", empty_db, 0) is True
        assert is_noise_token("do", empty_db, 0) is True
        assert is_noise_token("we", empty_db, 0) is True

    def test_domain_term_not_noise_empty_corpus(self, empty_db: sqlite3.Connection) -> None:
        assert is_noise_token("jwt", empty_db, 0) is False
        assert is_noise_token("rollback", empty_db, 0) is False
        assert is_noise_token("migration", empty_db, 0) is False

    def test_domain_term_not_noise_when_low_frequency(self, empty_db: sqlite3.Connection) -> None:
        # Layer 2 is gated at _MIN_CORPUS_FOR_IDF (50). With corpus_size < 50,
        # only Layer 1 fires. Domain terms are always signal in small corpora.
        for i in range(8):
            empty_db.execute(
                "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
                (f"generic content about deployment {i}", "p", "s", ""),
            )
        empty_db.execute(
            "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
            ("auth token validation", "p", "s", ""),
        )
        empty_db.execute(
            "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
            ("authentication middleware auth", "p", "s", ""),
        )
        empty_db.commit()
        # auth in 2/10 = 20% → SIGNAL regardless of threshold (corpus too small for Layer 2)
        assert is_noise_token("auth", empty_db, 10, threshold=0.4) is False
        # Even at 100% frequency on a small corpus, Layer 2 is gated
        assert is_noise_token("auth", empty_db, 10, threshold=0.0) is False

    def test_high_frequency_token_is_noise_layer2(self, empty_db: sqlite3.Connection) -> None:
        # Insert 5 docs all containing "boilerplate" → 100% frequency
        for i in range(5):
            empty_db.execute(
                "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
                (f"boilerplate header content {i}", "p", "s", ""),
            )
        empty_db.commit()
        # Pass corpus_size=50 to activate Layer 2 (5/50 = 10% — not noise)
        # and corpus_size=5 to confirm Layer 2 is gated
        assert is_noise_token("boilerplate", empty_db, 5, threshold=0.4) is False  # gated
        # Now simulate a 50-doc corpus where boilerplate is in all 50 docs
        # by reporting corpus_size=5 with threshold=0.0 (always fires above min)
        assert is_noise_token("boilerplate", empty_db, 50, threshold=0.04) is True

    def test_zero_corpus_size_never_noise_via_idf(self, empty_db: sqlite3.Connection) -> None:
        # Small corpus (< _MIN_CORPUS_FOR_IDF) → Layer 2 never fires → signal
        assert is_noise_token("deploy", empty_db, 0) is False
        assert is_noise_token("deploy", empty_db, 49) is False

    def test_threshold_respected(self, empty_db: sqlite3.Connection) -> None:
        # 3 docs all containing "common". Use corpus_size=50 to activate Layer 2.
        for i in range(3):
            empty_db.execute(
                "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
                (f"common word in every doc {i}", "p", "s", ""),
            )
        empty_db.commit()
        # 3/50 = 6% at threshold=0.8 → signal (low relative frequency)
        assert is_noise_token("common", empty_db, 50, threshold=0.8) is False
        # 3/3 = 100% relative to actual docs but corpus_size=50 → 6% → below threshold
        # To test noise: declare corpus_size=3 but it's < MIN → still gated → signal
        assert is_noise_token("common", empty_db, 3, threshold=0.0) is False  # gated
        # Activate Layer 2 with corpus_size=50 and a very low threshold
        assert is_noise_token("common", empty_db, 50, threshold=0.04) is True


# ---------------------------------------------------------------------------
# partition_query — full two-layer partitioning
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_store() -> MemoryStore:
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(db_path=Path(d) / "test.db")
        # 5 memories — enough to test layer 2 at small scale
        store.add("JWT token validation middleware for auth endpoints.", "f.md", "proj")
        store.add("Structured logging speeds up debugging.", "f.md", "proj")
        store.add("Docker deployment checklist for production rollout.", "f.md", "proj")
        store.add("Database migration rollback with Alembic.", "f.md", "proj")
        store.add("Rotate API credentials every 90 days.", "f.md", "proj")
        yield store
        store.close()


class TestPartitionQuery:
    def test_preposition_up_is_noise(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        signal, noise = partition_query(["set", "up", "auth"], real_store.db, cs)
        assert "up" in noise
        assert "set" in signal
        assert "auth" in signal

    def test_preposition_in_is_noise(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        signal, noise = partition_query(["check", "in", "code", "changes"], real_store.db, cs)
        assert "in" in noise
        assert "check" in signal
        assert "code" in signal

    def test_all_closed_class_returns_empty_signal(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        signal, noise = partition_query(["do", "we", "this", "is"], real_store.db, cs)
        assert signal == []
        assert set(noise) == {"do", "we", "this", "is"}

    def test_domain_terms_all_signal(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        terms = ["jwt", "rollback", "migration", "credential", "docker"]
        signal, noise = partition_query(terms, real_store.db, cs)
        assert set(signal) == set(terms)
        assert noise == []

    def test_output_order_preserved(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        tokens = ["authentication", "in", "the", "database"]
        signal, noise = partition_query(tokens, real_store.db, cs)
        # signal preserves input order
        assert signal == ["authentication", "database"]
        assert noise == ["in", "the"]

    def test_empty_token_list(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        signal, noise = partition_query([], real_store.db, cs)
        assert signal == []
        assert noise == []

    def test_numeric_token_is_signal(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        signal, noise = partition_query(["rotate", "every", "90", "days"], real_store.db, cs)
        # "every" is in DETERMINERS → noise
        assert "every" in noise
        assert "rotate" in signal
        assert "90" in signal

    def test_mixed_query_separates_correctly(self, real_store: MemoryStore) -> None:
        cs = real_store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        # "what is the best way to deploy"
        tokens = ["what", "is", "the", "best", "way", "to", "deploy"]
        signal, noise = partition_query(tokens, real_store.db, cs)
        assert "what" in noise   # pronoun
        assert "is" in noise     # auxiliary
        assert "the" in noise    # article
        assert "to" in noise     # preposition
        assert "deploy" in signal
        assert "best" in signal
        assert "way" in signal

    def test_zero_corpus_size_uses_layer1_only(self, real_store: MemoryStore) -> None:
        # corpus_size < _MIN_CORPUS_FOR_IDF → Layer 2 never fires, only Layer 1
        signal, noise = partition_query(["set", "up", "deploy"], real_store.db, 0)
        assert "up" in noise       # Layer 1 catches it (preposition)
        assert "set" in signal     # Not in closed-class, Layer 2 gated → signal
        assert "deploy" in signal

    def test_threshold_controls_layer2(self, empty_db: sqlite3.Connection) -> None:
        # Insert 60 docs, "common" appears in 36 (60%) — corpus_size=60 activates Layer 2
        for i in range(24):
            empty_db.execute(
                "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
                (f"content without the word {i}", "p", "s", ""),
            )
        for i in range(36):
            empty_db.execute(
                "INSERT INTO memories_fts(content, project, section, keywords) VALUES (?,?,?,?)",
                (f"common word here {i}", "p", "s", ""),
            )
        empty_db.commit()
        # At threshold=0.4: 60% > 40% → noise (corpus_size=60 > _MIN_CORPUS_FOR_IDF)
        s, n = partition_query(["common", "deploy"], empty_db, 60, threshold=0.4)
        assert "common" in n
        assert "deploy" in s
        # At threshold=0.7: 60% < 70% → signal
        s, n = partition_query(["common", "deploy"], empty_db, 60, threshold=0.7)
        assert "common" in s


# ---------------------------------------------------------------------------
# CONVERSATIONAL_FILLER — used by hooks.py for prompt token filtering
# ---------------------------------------------------------------------------


class TestConversationalFiller:
    def test_filler_combined_with_closed_class_covers_prompt_noise(self) -> None:
        combined = CLOSED_CLASS | CONVERSATIONAL_FILLER
        # A typical noisy AI prompt: "yes please go ahead and deploy the thing"
        tokens = ["yes", "please", "go", "ahead", "and", "deploy", "the", "thing"]
        signal = [t for t in tokens if t not in combined]
        assert signal == ["go", "deploy"], (
            f"Expected only 'go' and 'deploy' to survive, got {signal}"
        )

    def test_domain_terms_not_in_conversational_filler(self) -> None:
        domain_terms = ["deploy", "rollback", "migrate", "jwt", "auth", "docker"]
        for term in domain_terms:
            assert term not in CONVERSATIONAL_FILLER, (
                f"Domain term '{term}' incorrectly in CONVERSATIONAL_FILLER"
            )

    def test_key_filler_words_present(self) -> None:
        for word in ("yes", "ok", "please", "thanks", "just", "really", "stuff", "things"):
            assert word in CONVERSATIONAL_FILLER
