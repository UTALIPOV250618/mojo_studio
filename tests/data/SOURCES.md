# Test audio sources

| File | Source | Licence | What was done |
| --- | --- | --- | --- |
| `applause_cc0_16k.wav` | Wikimedia Commons, [File:Sound Effects - Applause after a concert.ogg](https://commons.wikimedia.org/wiki/File:Sound_Effects_-_Applause_after_a_concert.ogg), author Amada44 (original sha1 4e9a9f7235b36cc46702f40934d8949449cbeca4, 58.8 s) | CC0 1.0 | 10 s from 5 s, mono, 16 kHz, pcm_s16le (`ffmpeg -ss 5 -t 10 -ac 1 -ar 16000 -c:a pcm_s16le`); sha256 89c2a174ee17207733048727da4e52229360fbd09e022c3097aa40c11b19dbcd |

Downloaded 2026-11-09 with the owner's approval, for the YAMNet applause test.
