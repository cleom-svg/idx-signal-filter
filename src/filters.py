"""The filter funnel.

Stages run in ascending order of computational cost, so expensive work only
ever touches a small remainder:

    raw -> deduplicate -> statistical surprise -> watchlist -> digest

Every function here is PURE (no DB, no network) so it can be unit-tested with
synthetic data. That is deliberate — see tests at the bottom of this file.
"""
import logging
import math

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from config import (
    DEDUP_SIMILARITY,
    MIN_BASELINE_OBS,
    RETURN_Z_THRESHOLD,
    VOLUME_Z_THRESHOLD,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------- stage 1: dedupe

def deduplicate(items, threshold=DEDUP_SIMILARITY):
    """Collapse near-duplicate news into clusters, keeping the earliest member.

    One IDX announcement becomes 40 articles across Kontan, Bisnis, CNBC
    Indonesia and social reposts. This is one piece of information wearing 40
    costumes, and removing the costumes is the single biggest volume win.

    TF-IDF + cosine, NOT sentence-transformers. Transformers pull in PyTorch
    (~800MB) which CI would download on every run. Lexical overlap is plenty
    for near-identical headlines about the same event.

    SYMBOL-AWARE: items about different companies are NEVER duplicates, even
    when the wording is identical. "GOTO halted pending announcement" and
    "MDKA halted pending announcement" are two distinct events that score ~0.9
    cosine similarity. Without this partition the filter silently swallows real
    signal — which is exactly the failure mode we care most about.

    Returns (kept_items, groups) where groups maps item id -> cluster id.
    """
    if len(items) < 2:
        return list(items), {it["id"]: i for i, it in enumerate(items)}

    # Partition by symbol, cluster within each partition, then recombine.
    buckets = {}
    for it in items:
        buckets.setdefault((it.get("symbol") or "").upper(), []).append(it)

    if len(buckets) > 1:
        kept_all, groups_all, offset = [], {}, 0
        for _symbol, bucket in buckets.items():
            kept, groups = _dedup_one_bucket(bucket, threshold)
            kept_all.extend(kept)
            groups_all.update({k: v + offset for k, v in groups.items()})
            offset += len(kept)
        log.info("dedup: %d -> %d items", len(items), len(kept_all))
        return kept_all, groups_all

    kept, groups = _dedup_one_bucket(items, threshold)
    log.info("dedup: %d -> %d items", len(items), len(kept))
    return kept, groups


def _dedup_one_bucket(items, threshold):
    """Cluster a set of items already known to share a symbol."""
    if len(items) < 2:
        return list(items), {it["id"]: i for i, it in enumerate(items)}

    texts = [f"{it.get('title', '')} {it.get('body') or ''}".strip() for it in items]

    vectorizer = TfidfVectorizer(
        lowercase=True,
        ngram_range=(1, 2),
        min_df=1,
        strip_accents="unicode",
    )
    matrix = vectorizer.fit_transform(texts)
    sim = cosine_similarity(matrix)

    # Greedy single-pass clustering. O(n^2) but n is a few thousand at most.
    order = sorted(range(len(items)), key=lambda i: items[i].get("published_at") or "")
    assigned = {}
    representatives = []

    for idx in order:
        placed = False
        for cluster_id, rep_idx in enumerate(representatives):
            if sim[idx][rep_idx] >= threshold:
                assigned[items[idx]["id"]] = cluster_id
                placed = True
                break
        if not placed:
            assigned[items[idx]["id"]] = len(representatives)
            representatives.append(idx)

    kept = [items[i] for i in representatives]
    return kept, assigned


# ------------------------------------------------------- stage 2: surprise

def zscore(value, mean, std):
    """Guarded z-score. Returns None when the baseline is unusable."""
    if value is None or mean is None or std is None:
        return None
    if std == 0 or (isinstance(std, float) and math.isclose(std, 0.0)):
        return None
    return (float(value) - float(mean)) / float(std)


def score_surprise(item, baseline):
    """Attach volume_z and return_z to an item.

    Information is surprise. A number matching its own recent expectation
    carries none, no matter how many articles were written about it.
    """
    if not baseline or baseline.get("n_obs", MIN_BASELINE_OBS) < MIN_BASELINE_OBS:
        return {**item, "volume_z": None, "return_z": None}

    return {
        **item,
        "volume_z": zscore(
            baseline.get("volume"),
            baseline.get("vol_mean_30d"),
            baseline.get("vol_std_30d"),
        ),
        "return_z": zscore(
            baseline.get("daily_return"),
            baseline.get("ret_mean_30d"),
            baseline.get("ret_std_30d"),
        ),
    }


def is_surprising(item, vol_t=VOLUME_Z_THRESHOLD, ret_t=RETURN_Z_THRESHOLD):
    vz, rz = item.get("volume_z"), item.get("return_z")
    if vz is not None and abs(vz) >= vol_t:
        return True
    if rz is not None and abs(rz) >= ret_t:
        return True
    return False


def filter_surprising(items, **kw):
    kept = [it for it in items if is_surprising(it, **kw)]
    log.info("surprise: %d -> %d items", len(items), len(kept))
    return kept


# ------------------------------------------------------ stage 3: watchlist

def filter_watchlist(items, watchlist):
    """Trivial join, kills most of what survived the earlier stages."""
    universe = {s.upper() for s in watchlist}
    kept = [it for it in items if (it.get("symbol") or "").upper() in universe]
    log.info("watchlist: %d -> %d items", len(items), len(kept))
    return kept


# ------------------------------------------------------------ ranking

def rank(items, limit):
    """Strongest anomaly first. Hard cap on count — alert fatigue is the
    failure mode that kills every alerting system ever built."""

    def strength(it):
        return max(abs(it.get("volume_z") or 0), abs(it.get("return_z") or 0))

    return sorted(items, key=strength, reverse=True)[:limit]


def describe_reason(item):
    vz, rz = item.get("volume_z"), item.get("return_z")
    parts = []
    if vz is not None and abs(vz) >= VOLUME_Z_THRESHOLD:
        parts.append(f"volume {vz:+.1f}s")
    if rz is not None and abs(rz) >= RETURN_Z_THRESHOLD:
        parts.append(f"price {rz:+.1f}s")
    return ", ".join(parts) or "watchlist"
