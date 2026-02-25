"""
Semantic Cache for BSB Voice AI
================================
Stores LLM answers indexed by sentence embeddings so that semantically
similar incoming queries (e.g. "BSB kya hai?" and "BSB ke baare mein batao")
reuse the cached answer instead of hitting Groq.

Lookup:  ~1-5 ms (FAISS in-memory cosine search)
vs. LLM: 1500-4000 ms (Groq API round-trip)

Design:
  - Uses sentence-transformers `all-MiniLM-L6-v2` (~80MB, runs on CPU)
  - FAISS IndexFlatIP for cosine similarity (after L2 normalisation)
  - Max 200 entries; oldest entry evicted when full (FIFO)
  - Similarity threshold 0.90 (tight — avoids wrong matches on product/price queries)
  - Thread-safe via asyncio.Lock (safe for FastAPI async handlers)
  - Dynamic queries that contain digits (prices, quantities) are NOT cached
    because they are caller-specific and must always hit the LLM.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from typing import Deque, List, Optional

import numpy as np

logger = logging.getLogger("semantic_cache")

# ── Lazy imports (heavy libraries loaded once on first use) ───────────────────
_embedder = None
_faiss_index = None
_EMBED_DIM = 384  # all-MiniLM-L6-v2 output dimension


def _get_embedder():
    """Load sentence-transformer model (downloaded once, then cached locally)."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("[CACHE] Loading sentence-transformer model (first run may download ~80MB)...")
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("[CACHE] Model loaded successfully.")
        except ImportError:
            logger.warning("[CACHE] sentence-transformers not installed. Semantic cache disabled.")
    return _embedder


def _get_faiss_index():
    """Create a fresh FAISS inner-product index (cosine after normalisation)."""
    global _faiss_index
    if _faiss_index is None:
        try:
            import faiss
            _faiss_index = faiss.IndexFlatIP(_EMBED_DIM)
            logger.info("[CACHE] FAISS index initialised.")
        except ImportError:
            logger.warning("[CACHE] faiss-cpu not installed. Semantic cache disabled.")
    return _faiss_index


# ── Helpers ───────────────────────────────────────────────────────────────────

_DIGIT_RE = re.compile(r'\d')


def _is_dynamic_query(text: str) -> bool:
    """
    Return True for queries containing numbers (prices, quantities, phone numbers).
    These are caller-specific and must never be served from cache.
    """
    return bool(_DIGIT_RE.search(text))


def _embed(text: str) -> Optional[np.ndarray]:
    """Return L2-normalised embedding vector or None if embedder unavailable."""
    embedder = _get_embedder()
    if embedder is None:
        return None
    vec = embedder.encode([text], convert_to_numpy=True, normalize_embeddings=True)
    return vec.astype(np.float32)  # shape: (1, 384)


# ── Cache class ───────────────────────────────────────────────────────────────

class SemanticCache:
    """
    In-memory semantic cache backed by FAISS.

    Usage:
        answer = await semantic_cache.get("BSB kya hai?")
        if answer:
            ... # use cached answer
        else:
            answer = await call_llm(...)
            await semantic_cache.put("BSB kya hai?", answer)
    """

    MAX_SIZE = 200
    SIMILARITY_THRESHOLD = 0.90

    def __init__(self):
        self._lock = asyncio.Lock()
        # Parallel list: index i in FAISS → _entries[i] = (original_query, cached_answer)
        self._entries: Deque[tuple[str, str]] = deque()
        self._enabled: Optional[bool] = None  # determined lazily
        logger.info("[CACHE] Semantic cache initialised (lazy-loading embedder).")

    def _check_enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = (
                _get_embedder() is not None and
                _get_faiss_index() is not None
            )
            if self._enabled:
                logger.info("[CACHE] Semantic cache ENABLED.")
            else:
                logger.warning("[CACHE] Semantic cache DISABLED — install faiss-cpu and sentence-transformers.")
        return self._enabled

    async def get(self, query: str) -> Optional[str]:
        """
        Return cached answer if a semantically similar query exists, else None.
        Always returns None for dynamic queries (containing digits).
        """
        if _is_dynamic_query(query):
            return None

        if not self._check_enabled():
            return None

        index = _get_faiss_index()
        if index.ntotal == 0:
            return None

        loop = asyncio.get_running_loop()
        vec = await loop.run_in_executor(None, _embed, query)
        if vec is None:
            return None

        async with self._lock:
            # FAISS search returns top-1 match
            distances, indices = await loop.run_in_executor(
                None, lambda: index.search(vec, 1)
            )
            score = float(distances[0][0])
            idx   = int(indices[0][0])

            if score >= self.SIMILARITY_THRESHOLD and idx < len(self._entries):
                original_q, cached_ans = list(self._entries)[idx]
                logger.info(
                    f"[CACHE] HIT (score={score:.3f}) | "
                    f"Query: '{query[:50]}' | "
                    f"Matched: '{original_q[:50]}'"
                )
                return cached_ans

            logger.debug(f"[CACHE] MISS (best_score={score:.3f}) for: '{query[:50]}'")
            return None

    async def put(self, query: str, answer: str):
        """Embed query and store (query, answer) pair in the index."""
        if _is_dynamic_query(query):
            return  # never cache dynamic/numeric queries

        if not self._check_enabled():
            return

        if not answer or not answer.strip():
            return

        index = _get_faiss_index()
        loop  = asyncio.get_running_loop()

        vec = await loop.run_in_executor(None, _embed, query)
        if vec is None:
            return

        async with self._lock:
            if len(self._entries) >= self.MAX_SIZE:
                # Evict oldest: rebuild index without first entry
                self._entries.popleft()
                all_queries = [q for q, _ in self._entries]
                new_index_data = await loop.run_in_executor(
                    None,
                    lambda: np.vstack([
                        _embed(q) for q in all_queries
                    ]) if all_queries else np.empty((0, _EMBED_DIM), dtype=np.float32)
                )
                index.reset()
                if len(new_index_data) > 0:
                    index.add(new_index_data)
                logger.info("[CACHE] Evicted oldest entry (cache full).")

            await loop.run_in_executor(None, lambda: index.add(vec))
            self._entries.append((query, answer))
            logger.info(f"[CACHE] STORED (total={len(self._entries)}): '{query[:50]}'")

    def clear(self):
        """Clear all cached entries (useful for testing)."""
        index = _get_faiss_index()
        if index:
            index.reset()
        self._entries.clear()
        logger.info("[CACHE] Cache cleared.")

    @property
    def size(self) -> int:
        return len(self._entries)


# ── Singleton ─────────────────────────────────────────────────────────────────
semantic_cache = SemanticCache()
