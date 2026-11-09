"""Download or verify the bundled ONNX models.

python tools/fetch_models.py            # download missing/mismatching files
python tools/fetch_models.py --verify   # only check sha256, never download
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "toystudio" / "engine" / "models"

# name -> (url, sha256, filename)
MODELS: dict[str, tuple[str, str, str]] = {
    "silero_vad": (
        "https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/src/silero_vad/data/silero_vad.onnx",
        "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
        "silero_vad.onnx",
    ),
    "yamnet": (
        "https://huggingface.co/audiomagic/yamnet-onnx/resolve/f25b741c2f0bdc6d7e6db24b5fddda23347dbafd/yamnet.onnx",
        "d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e",
        "yamnet.onnx",
    ),
    "yamnet_class_map": (
        "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv",
        "cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2",
        "yamnet_class_map.csv",
    ),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ok(path: Path, sha: str) -> bool:
    return path.is_file() and _sha256(path) == sha


def _download(url: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, part.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    os.replace(part, dest)


def main(argv: list[str] | None = None, models_dir: Path = MODELS_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check sha256 only, no download")
    args = parser.parse_args(argv)
    failed = 0
    for name, (url, sha, filename) in MODELS.items():
        dest = models_dir / filename
        if _ok(dest, sha):
            print(f"ok       {name}")
            continue
        if args.verify:
            print(f"MISMATCH {name}: {dest} missing or sha256 differs", file=sys.stderr)
            failed += 1
            continue
        print(f"download {name} <- {url}")
        _download(url, dest)
        if not _ok(dest, sha):
            dest.unlink(missing_ok=True)
            print(f"MISMATCH {name}: downloaded file has a wrong sha256", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
