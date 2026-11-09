"""Grouping of the same moment seen by different cameras."""

from toystudio.core.models import MomentRow

MIN_OVERLAP = 0.5  # share of the shorter moment that must overlap


def group_ids(
    moments: list[MomentRow], offsets_us: dict[int, int | None]
) -> list[tuple[int, int | None]]:
    """(moment id, group id) in input order.

    Moments are placed on the reference timeline with their file's sync offset (the reference
    camera has offset 0). Moments of different files overlapping by >= 50 % of the shorter one
    share a group; the group id is the smallest member id. A moment whose file has no known
    offset gets None; a moment alone in its group gets its own id.
    """
    placed: dict[int, tuple[int, int, int]] = {}  # index -> (media_id, start, end)
    for i, m in enumerate(moments):
        off = offsets_us.get(m.media_id)
        if off is not None:
            placed[i] = (m.media_id, m.start_us + off, m.end_us + off)

    parent = {i: i for i in placed}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    idx = sorted(placed)
    for n, i in enumerate(idx):
        for j in idx[n + 1 :]:
            mi, si, ei = placed[i]
            mj, sj, ej = placed[j]
            if mi == mj:
                continue
            overlap = min(ei, ej) - max(si, sj)
            if overlap > 0 and overlap >= MIN_OVERLAP * min(ei - si, ej - sj):
                parent[find(i)] = find(j)

    smallest: dict[int, int] = {}
    for i in idx:
        root = find(i)
        smallest[root] = min(smallest.get(root, moments[i].id), moments[i].id)
    return [(m.id, smallest[find(i)] if i in placed else None) for i, m in enumerate(moments)]
