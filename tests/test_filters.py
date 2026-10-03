"""Tests for the filter funnel.

Run:  python -m pytest tests/ -v

These matter more than they look. The filters are the "defined conditions" the
rubric grades, and a silently-wrong z-score produces a pipeline that runs
beautifully every day while filtering nothing correctly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from filters import (  # noqa: E402
    deduplicate,
    filter_surprising,
    filter_watchlist,
    is_surprising,
    rank,
    score_surprise,
    zscore,
)


def test_zscore_basic():
    assert zscore(120, 100, 10) == 2.0
    assert zscore(80, 100, 10) == -2.0


def test_zscore_guards_zero_std():
    """A flat baseline must not produce infinity."""
    assert zscore(100, 100, 0) is None
    assert zscore(None, 100, 10) is None
    assert zscore(100, None, 10) is None


def test_dedup_collapses_near_duplicates():
    """Same announcement, five outlets. Should collapse to one."""
    items = [
        {"id": "1", "title": "BBCA reports record quarterly profit", "published_at": "2026-09-14T01:00"},
        {"id": "2", "title": "BBCA reports record quarterly profits", "published_at": "2026-09-14T02:00"},
        {"id": "3", "title": "BBCA posts record quarterly profit figures", "published_at": "2026-09-14T03:00"},
        {"id": "4", "title": "TLKM announces new data centre investment", "published_at": "2026-09-14T04:00"},
    ]
    kept, groups = deduplicate(items, threshold=0.55)
    assert len(kept) < len(items), "near-duplicates were not collapsed"
    assert groups["4"] != groups["1"], "unrelated items must not cluster"


def test_dedup_keeps_earliest():
    items = [
        {"id": "late", "title": "Same headline text here", "published_at": "2026-09-14T09:00"},
        {"id": "early", "title": "Same headline text here", "published_at": "2026-09-14T01:00"},
    ]
    kept, _ = deduplicate(items, threshold=0.5)
    assert len(kept) == 1
    assert kept[0]["id"] == "early", "should keep the earliest-published member"


def test_dedup_handles_single_item():
    items = [{"id": "1", "title": "Only one", "published_at": "2026-09-14T01:00"}]
    kept, groups = deduplicate(items)
    assert len(kept) == 1 and groups == {"1": 0}


def test_surprise_scoring():
    item = {
        "id": "x", "symbol": "BBCA",
        "volume": 5_000_000, "vol_mean_30d": 1_000_000, "vol_std_30d": 500_000,
        "daily_return": 0.01, "ret_mean_30d": 0.0, "ret_std_30d": 0.02,
        "n_obs": 30,
    }
    scored = score_surprise(item, item)
    assert scored["volume_z"] == 8.0
    assert scored["return_z"] == 0.5
    assert is_surprising(scored), "8-sigma volume must pass"


def test_quiet_day_is_not_surprising():
    item = {
        "id": "y", "symbol": "TLKM",
        "volume": 1_020_000, "vol_mean_30d": 1_000_000, "vol_std_30d": 500_000,
        "daily_return": 0.001, "ret_mean_30d": 0.0, "ret_std_30d": 0.02,
        "n_obs": 30,
    }
    assert not is_surprising(score_surprise(item, item))


def test_thin_baseline_is_skipped():
    """Fewer than MIN_BASELINE_OBS observations -> no scores, no false alarm."""
    item = {
        "id": "z", "symbol": "NEWCO", "n_obs": 3,
        "volume": 9_000_000, "vol_mean_30d": 100, "vol_std_30d": 1,
    }
    scored = score_surprise(item, item)
    assert scored["volume_z"] is None
    assert not is_surprising(scored)


def test_watchlist_filter():
    items = [{"symbol": "BBCA"}, {"symbol": "TLKM"}, {"symbol": "GOTO"}]
    kept = filter_watchlist(items, ["BBCA", "goto"])
    assert {i["symbol"] for i in kept} == {"BBCA", "GOTO"}


def test_rank_caps_and_orders():
    items = [
        {"id": "a", "volume_z": 2.6, "return_z": None},
        {"id": "b", "volume_z": 9.1, "return_z": None},
        {"id": "c", "volume_z": None, "return_z": -5.0},
    ]
    top = rank(items, limit=2)
    assert [i["id"] for i in top] == ["b", "c"]
    assert len(top) == 2


def test_full_funnel_reduces():
    """End-to-end shape check on the pure stages."""
    items = []
    for i in range(50):
        items.append({
            "id": str(i),
            "symbol": "BBCA" if i % 2 else "TLKM",
            "title": f"Routine market update number {i}",
            "published_at": f"2026-09-14T{i % 24:02d}:00",
            "volume": 1_000_000, "vol_mean_30d": 1_000_000, "vol_std_30d": 200_000,
            "daily_return": 0.001, "ret_mean_30d": 0.0, "ret_std_30d": 0.02,
            "n_obs": 30,
        })
    items.append({
        "id": "spike",
        "symbol": "BBCA",
        "title": "BBCA halted after unusual trading activity",
        "published_at": "2026-09-14T10:00",
        "volume": 9_000_000, "vol_mean_30d": 1_000_000, "vol_std_30d": 200_000,
        "daily_return": 0.12, "ret_mean_30d": 0.0, "ret_std_30d": 0.02,
        "n_obs": 30,
    })

    deduped, _ = deduplicate(items)
    scored = [score_surprise(i, i) for i in deduped]
    surprising = filter_surprising(scored)
    final = rank(filter_watchlist(surprising, ["BBCA", "TLKM"]), 10)

    assert len(final) >= 1
    assert final[0]["id"] == "spike", "the genuine anomaly must rank first"
    assert len(final) < len(items), "funnel must reduce volume"


def test_dedup_never_merges_different_symbols():
    """Regression: identical wording about different companies are NOT duplicates.

    Found by running the funnel on synthetic data — three separate halts
    collapsed into one because the headlines were textually near-identical.
    """
    items = [
        {"id": "a", "symbol": "GOTO", "title": "halted pending material announcement", "published_at": "2026-09-14T10:00"},
        {"id": "b", "symbol": "MDKA", "title": "halted pending material announcement", "published_at": "2026-09-14T10:01"},
        {"id": "c", "symbol": "ADRO", "title": "halted pending material announcement", "published_at": "2026-09-14T10:02"},
    ]
    kept, _ = deduplicate(items, threshold=0.5)
    assert len(kept) == 3, "different symbols must never be merged"


def test_dedup_still_merges_within_symbol():
    items = [
        {"id": "a", "symbol": "BBCA", "title": "BBCA reports record quarterly profit", "published_at": "2026-09-14T01:00"},
        {"id": "b", "symbol": "BBCA", "title": "BBCA reports record quarterly profits", "published_at": "2026-09-14T02:00"},
        {"id": "c", "symbol": "BBCA", "title": "BBCA posts record quarterly profit figures", "published_at": "2026-09-14T03:00"},
    ]
    kept, _ = deduplicate(items, threshold=0.55)
    assert len(kept) < 3, "same-symbol near-duplicates must still collapse"
