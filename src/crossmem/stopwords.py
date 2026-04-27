"""Two-layer token noise filter for crossmem search.

Layer 1 — Closed-class English words
--------------------------------------
English has six "closed" word classes: articles, prepositions, pronouns,
conjunctions, auxiliaries, and determiners.  These classes are called
"closed" because membership is linguistically fixed — no new words have
been coined in these categories in centuries.  They carry no searchable
meaning and should always be excluded from FTS queries.

Layer 2 — Corpus-adaptive IDF
------------------------------
Any token that appears in more than `threshold` fraction of documents is
corpus-specific noise (e.g. a word used in the boilerplate of every
memory).  Computed at query-time via ``FTS5 MATCH`` count, which handles
porter stemming automatically.

Design constraints
------------------
- Zero external dependencies (pure Python + SQLite FTS5).
- ``CLOSED_CLASS`` and ``CONVERSATIONAL_FILLER`` are frozensets: O(1)
  lookup, picklable, hashable.
- Layer 2 fires one SQL query per token — sub-millisecond on FTS5.
  Only use ``partition_query`` for small token lists (query-time use).
  For batch content scanning use ``CLOSED_CLASS`` directly.
"""

from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# Layer 1: Closed-class English words
# ---------------------------------------------------------------------------

ARTICLES: frozenset[str] = frozenset({"a", "an", "the"})

PREPOSITIONS: frozenset[str] = frozenset(
    {
        "about",
        "above",
        "across",
        "after",
        "against",
        "along",
        "among",
        "around",
        "at",
        "before",
        "behind",
        "below",
        "beneath",
        "beside",
        "between",
        "beyond",
        "by",
        "down",
        "during",
        "except",
        "for",
        "from",
        "in",
        "inside",
        "into",
        "like",
        "near",
        "of",
        "off",
        "on",
        "onto",
        "out",
        "outside",
        "over",
        "past",
        "since",
        "through",
        "throughout",
        "till",
        "to",
        "toward",
        "towards",
        "under",
        "underneath",
        "until",
        "up",
        "upon",
        "with",
        "within",
        "without",
    }
)

PRONOUNS: frozenset[str] = frozenset(
    {
        "i",
        "me",
        "my",
        "mine",
        "myself",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "he",
        "him",
        "his",
        "himself",
        "she",
        "her",
        "hers",
        "herself",
        "it",
        "its",
        "itself",
        "we",
        "us",
        "our",
        "ours",
        "ourselves",
        "they",
        "them",
        "their",
        "theirs",
        "themselves",
        "who",
        "whom",
        "whose",
        "which",
        "that",
        "this",
        "these",
        "those",
        "what",
        "whatever",
        "whichever",
        "whoever",
        "whomever",
        "each",
        "every",
        "either",
        "neither",
        "both",
        "all",
        "any",
        "few",
        "many",
        "several",
        "some",
        "none",
        "other",
        "another",
    }
)

CONJUNCTIONS: frozenset[str] = frozenset(
    {
        "and",
        "but",
        "or",
        "nor",
        "for",
        "yet",
        "so",
        "although",
        "because",
        "if",
        "once",
        "since",
        "than",
        "though",
        "unless",
        "until",
        "when",
        "whenever",
        "where",
        "wherever",
        "while",
        "whether",
    }
)

AUXILIARIES: frozenset[str] = frozenset(
    {
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "can",
        "could",
        "must",
        "need",
        "dare",
        "ought",
    }
)

DETERMINERS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "my",
        "your",
        "his",
        "her",
        "its",
        "our",
        "their",
        "much",
        "many",
        "more",
        "most",
        "less",
        "least",
        "few",
        "no",
        "not",
        "only",
        "own",
        "same",
        "such",
    }
)

# Union of all six categories — the primary export for batch filtering.
CLOSED_CLASS: frozenset[str] = (
    ARTICLES | PREPOSITIONS | PRONOUNS | CONJUNCTIONS | AUXILIARIES | DETERMINERS
)

# ---------------------------------------------------------------------------
# Conversational filler — hooks.py only
# ---------------------------------------------------------------------------
# These words are open-class (adverbs, adjectives, interjections) but act
# as pure filler in conversational AI prompts.  They are NOT removed from
# stored memory content — only from prompt tokens before search.
# Keep this list minimal: only include words that carry zero search value
# in prompts but would potentially have signal in stored memories.

CONVERSATIONAL_FILLER: frozenset[str] = frozenset(
    {
        # Affirmations / acknowledgements
        "yes",
        "yeah",
        "yep",
        "ok",
        "okay",
        "sure",
        "right",
        "good",
        "great",
        "cool",
        "nice",
        "fine",
        "done",
        # Politeness markers
        "please",
        "thanks",
        "thank",
        # Hedges / intensifiers with no information value in prompts
        "just",
        "also",
        "really",
        "actually",
        "pretty",
        "well",
        "still",
        "ahead",
        "continue",
        # Location/time adverbs that add no search signal
        "here",
        "there",
        "now",
        "then",
        # Vague filler nouns
        "stuff",
        "things",
        "thing",
        "something",
        "anything",
        "interesting",
    }
)

# ---------------------------------------------------------------------------
# Layer 2: Corpus-adaptive IDF via FTS5 MATCH
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 0.4
# IDF is only meaningful at corpus scale. Below this size, every domain term
# is "high-frequency" relative to the few documents, producing false positives.
# Layer 2 is disabled until the corpus reaches this minimum.
_MIN_CORPUS_FOR_IDF = 50


def is_noise_token(
    token: str,
    db: sqlite3.Connection,
    corpus_size: int,
    threshold: float = _DEFAULT_THRESHOLD,
) -> bool:
    """Return True if *token* should be excluded from a search query.

    Applies both layers in order:

    1. If *token* is in ``CLOSED_CLASS`` → noise (O(1) set lookup).
    2. If the fraction of documents matching *token* exceeds *threshold*
       → corpus-adaptive noise.  Uses ``FTS5 MATCH`` so porter stemming
       is handled transparently.

    Only suitable for query-time use (small token counts).  For bulk
    content filtering use ``CLOSED_CLASS`` directly.
    """
    if token in CLOSED_CLASS:
        return True
    if corpus_size < _MIN_CORPUS_FOR_IDF:
        return False
    try:
        count = db.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            (token,),
        ).fetchone()[0]
    except Exception:
        return False
    return count / corpus_size > threshold


def partition_query(
    tokens: list[str],
    db: sqlite3.Connection,
    corpus_size: int,
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[list[str], list[str]]:
    """Split *tokens* into ``(signal, noise)`` using the two-layer filter.

    *signal* tokens are passed to FTS5 AND-of-ORs query construction.
    *noise* tokens are dropped from the structured query; BM25 still
    ranks them naturally when they appear in result content.

    Args:
        tokens: lowercased alphanumeric tokens from the user query.
        db: open SQLite connection with ``memories_fts`` FTS5 table.
        corpus_size: total document count (``SELECT COUNT(*) FROM memories``).
        threshold: IDF threshold — tokens in more than this fraction of
            documents are treated as corpus noise.  Default 0.4.

    Returns:
        A ``(signal, noise)`` tuple of token lists preserving input order.
    """
    signal: list[str] = []
    noise: list[str] = []
    for token in tokens:
        if is_noise_token(token, db, corpus_size, threshold):
            noise.append(token)
        else:
            signal.append(token)
    return signal, noise
