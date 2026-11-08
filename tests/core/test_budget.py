from toystudio.core.budget import CACHE_BYTES_PER_MIN, EXPORT_RESERVE_BYTES, needed_bytes
from toystudio.core.models import US_PER_S


def test_needed_bytes_copy_and_cache() -> None:
    gib = 1024**3
    items = [(6 * gib, 110 * 60 * US_PER_S), (int(5.8 * gib), 110 * 60 * US_PER_S)]
    assert needed_bytes(items, copy=False) == 220 * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES
    assert (
        needed_bytes(items, copy=True)
        == 6 * gib + int(5.8 * gib) + 220 * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES
    )


def test_needed_bytes_rounds_duration_up_per_item() -> None:
    # 1 s and 61 s both round up: 1 min + 2 min = 3 min of cache
    items = [(0, US_PER_S), (0, 61 * US_PER_S)]
    assert needed_bytes(items, copy=False) == 3 * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES
