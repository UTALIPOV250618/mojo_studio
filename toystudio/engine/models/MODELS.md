# Bundled ONNX models

Committed so the installer and offline builds do not need the network.
`python tools/fetch_models.py` re-downloads them; `--verify` only checks sha256.
Run on CPU with `onnxruntime` (`CPUExecutionProvider`). Verified with onnxruntime 1.31.1.

## silero_vad.onnx (speech regions)

- Source: https://github.com/snakers4/silero-vad, tag `v5.1.2`, file `src/silero_vad/data/silero_vad.onnx`
- Version: Silero VAD v5 (opset-16 ONNX shipped in release v5.1.2)
- Licence: MIT, Copyright (c) 2020-present Silero Team (`LICENSE-silero-vad.txt`, copied from the repo at the same tag)
- Size: 2 327 524 bytes
- sha256: `2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f`
- Inputs (checked with `InferenceSession.get_inputs()`):
  - `input` float32 `[batch, samples]` (use `[1, 512]` at 16 kHz)
  - `state` float32 `[2, batch, 128]` (zeros for the first chunk)
  - `sr` int64 scalar `[]` (16000)
- Outputs:
  - `output` float32 `[batch, 1]`: speech probability (zeros input gives about 0.044)
  - `stateN` float32 `[2, batch, 128]`: new state, feed back as `state`

## yamnet.onnx (audio events)

- Source: https://huggingface.co/audiomagic/yamnet-onnx, revision `f25b741c2f0bdc6d7e6db24b5fddda23347dbafd`, file `yamnet.onnx`
- Origin: Google YAMNet v1 (tensorflow/models `research/audioset/yamnet`) converted with tf2onnx (opset 15), weights unchanged (per the model card)
- Licence: Apache-2.0 (model card metadata and repo `LICENSE`, 11 358 bytes, saved as `LICENSE-yamnet.txt`). Copyright 2022 Google LLC. Changes: format conversion only. AudioSet labels: CC BY 4.0.
- Size: 16 093 355 bytes
- sha256: `d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e`
- Input: `waveform` float32 `[num_samples]`, 1-D mono 16 kHz, nominal range [-1, 1], dynamic length (mel frontend is inside the graph)
- Outputs (one row per frame, hop 0.48 s, window 0.96 s):
  - `output_0` float32 `[frames, 521]`: class scores
  - `output_1` float32 `[frames, 1024]`: embeddings
  - `output_2` float32 `[frames*96, 64]`: log-mel patches
- Measured frames: 15600 samples -> 1, 16000 -> 2, 48000 -> 6. Formula: `frames = 1 + ceil(max(0, n - 15360) / 7680)`.

## yamnet_class_map.csv (521 AudioSet classes)

- Source: https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv (the URL tracks `master`; the content is pinned by sha256)
- Licence: Apache-2.0 (tensorflow/models); label ontology CC BY 4.0. See `LICENSE-yamnet.txt`.
- Size: 14 096 bytes
- sha256: `cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2`
- Columns: `index,mid,display_name`; 521 data rows; row order equals the `output_0` column order. Row 494 is `Silence` (zero input scores highest on it).
