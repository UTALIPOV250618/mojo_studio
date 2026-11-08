"""Disk budget estimate for a new project: source copy, proxy cache and export reserve."""

from toystudio.core.models import US_PER_S

# Proxy ~4.3 MB/min (S3) + wav 1.92 MB/min + metrics and envelope, with a 10 % margin.
CACHE_BYTES_PER_MIN = 6_800_000
EXPORT_RESERVE_BYTES = 2 * 1024**3

_US_PER_MIN = 60 * US_PER_S


def needed_bytes(items: list[tuple[int, int]], copy: bool) -> int:
    """Bytes needed for `items` given as (size_bytes, duration_us).

    Each duration is rounded up to whole minutes before summing.
    """
    source = sum(size for size, _ in items) if copy else 0
    cache_min = sum(-(-duration_us // _US_PER_MIN) for _, duration_us in items)
    return source + cache_min * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES
