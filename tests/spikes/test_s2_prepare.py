from spikes.s2_player.prepare import TITLE_TEXT, write_title_ass


def test_title_ass_has_inter_and_cyrillic(tmp_path):
    text = write_title_ass(tmp_path / "t.ass").read_text(encoding="utf-8")
    assert TITLE_TEXT == "Үйлөнүү тою — Өмүрбек"
    assert "Style: Title,Inter,48," in text
    assert f"Dialogue: 0,0:00:02.00,0:00:12.00,Title,,0,0,0,,{TITLE_TEXT}" in text
    style_fields = next(ln for ln in text.splitlines() if ln.startswith("Style:"))
    format_fields = next(ln for ln in text.splitlines() if ln.startswith("Format: Name"))
    assert style_fields.count(",") == format_fields.count(",")
