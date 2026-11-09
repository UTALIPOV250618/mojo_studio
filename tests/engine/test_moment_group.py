from toystudio.core.models import US_PER_S, MomentRow, MomentType, QualityLabel
from toystudio.engine.moments.group import group_ids

S = US_PER_S


def mom(id_, media, a, b):
    return MomentRow(id_, media, MomentType.TOAST, a * S, b * S, 0.7, QualityLabel.CLEAR)


def test_group_overlapping_moments_across_cameras():
    ms = [
        mom(5, 1, 100, 160),  # reference camera
        mom(3, 2, 70, 130),  # offset +30 -> 100..160 on the shared scale
        mom(9, 2, 300, 360),  # nothing else there
        mom(8, 1, 500, 520),
    ]
    out = group_ids(ms, {1: 0, 2: 30 * S})
    assert out == [(5, 3), (3, 3), (9, 9), (8, 8)]
    # overlap below half of the shorter moment: separate groups
    low = group_ids([mom(1, 1, 100, 160), mom(2, 2, 100, 160)], {1: 0, 2: 40 * S})
    assert low == [(1, 1), (2, 2)]
    # same file never groups with itself
    same = group_ids([mom(1, 1, 100, 160), mom(2, 1, 110, 150)], {1: 0})
    assert same == [(1, 1), (2, 2)]


def test_no_group_without_offset():
    ms = [mom(1, 1, 100, 160), mom(2, 2, 100, 160), mom(3, 3, 100, 160)]
    out = group_ids(ms, {1: 0, 2: None})  # file 3 is not in the dict at all
    assert out == [(1, 1), (2, None), (3, None)]
