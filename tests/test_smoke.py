import toystudio


def test_version() -> None:
    assert toystudio.__version__ == "0.1.0"


def test_repo_pins_onnxruntime_and_keeps_model_bytes() -> None:
    import shutil
    import subprocess
    import tomllib
    from pathlib import Path

    import pytest

    root = Path(__file__).resolve().parent.parent
    deps = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["dependencies"]
    assert "onnxruntime~=1.31" in deps  # MODELS.md row counts are verified with 1.31.1
    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    files = ["toystudio/engine/models/yamnet.onnx", "toystudio/engine/models/yamnet_class_map.csv",
             "tests/data/applause_cc0_16k.wav"]  # fmt: skip
    out = subprocess.run(
        [git, "check-attr", "text", "diff", "--", *files],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    for f in files:  # binary = -text -diff; the csv is hashed, so no EOL conversion either
        assert f"{f}: text: unset" in out, out


def test_readme_fetch_models_wording() -> None:
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text("utf-8")
    assert "без `--verify` скрипт докачает недостающие или повреждённые модели" in readme
