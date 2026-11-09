"""Bundled ONNX models (Silero VAD, YAMNet) and where to find them."""

from pathlib import Path
from typing import Literal

ModelName = Literal["silero_vad", "yamnet", "yamnet_class_map"]

MODELS_DIR = Path(__file__).resolve().parent

_FILES: dict[str, str] = {
    "silero_vad": "silero_vad.onnx",
    "yamnet": "yamnet.onnx",
    "yamnet_class_map": "yamnet_class_map.csv",
}


def model_path(name: ModelName) -> Path:
    path = MODELS_DIR / _FILES[name]
    if not path.is_file():
        raise FileNotFoundError(f"Model file {path} is missing; run: python tools/fetch_models.py")
    return path
