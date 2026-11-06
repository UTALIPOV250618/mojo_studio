from pathlib import Path

import pytest

from tools.gen_theme import contrast_ratio, load_tokens, main, render_qss

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ROOT / "design" / "tokens.json"
GENERATED = ROOT / "toystudio" / "app" / "theme" / "generated"


def test_load_dark_light():
    t = load_tokens(TOKENS)
    assert t.colors["dark"]["bg-000"] == "#0f1012"
    assert t.colors["light"]["garnet-500"] == "#a52a3a"
    assert t.sizes["size-topbar"] == 48 and t.sizes["radius-pill"] == 999
    assert t.type_styles["prompt"].font_size == 16


def test_unknown_placeholder_raises():
    with pytest.raises(KeyError, match="bg-999"):
        render_qss("QWidget{background:{{bg-999}}}", {"bg-000": "#000"}, {})


def test_scrim_rgba_passthrough():
    t = load_tokens(TOKENS)
    assert render_qss("{{scrim}}", t.colors["dark"], {}) == "rgba(0,0,0,0.6)"


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize(
    "fg,bg,minimum",
    [
        ("ink", "bg-100", 4.5),
        ("ink-muted", "bg-200", 4.5),
        ("ink-faint", "bg-100", 4.5),
        ("ink-on-accent", "garnet-500", 4.5),
        ("garnet-text", "bg-200", 4.5),
        ("line-200", "bg-200", 3.0),
        ("ink-on-title", "track-title", 4.5),
    ],
)
def test_contrast(theme, fg, bg, minimum):
    c = load_tokens(TOKENS).colors[theme]
    assert contrast_ratio(c[fg], c[bg]) >= minimum


def test_generated_is_fresh(tmp_path):
    main([str(TOKENS), str(tmp_path)])
    for name in ("theme_dark.qss", "theme_light.qss", "tokens.py"):
        assert (tmp_path / name).read_text("utf-8") == (GENERATED / name).read_text("utf-8")
