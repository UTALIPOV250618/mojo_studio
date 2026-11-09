# Той Студия — фаза 2б «Звук и моменты без распознавания речи»: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Анализ находит в записи участки речи и звуковые события (аплодисменты, музыка, крики, смех) и строит из них моменты (тост, танец, выход молодых, аплодисменты, музыка) с оценкой и объяснением; моменты появляются в живой ленте «Уже найдено» на экране анализа.

**Architecture:** Этап 2 (`StageName.SPEECH`) делится на два вычисления над `cache/audio/<id>.wav`: Silero VAD → участки речи, YAMNet → звуковые события; оба — onnxruntime на CPU, модели поставляются с программой. Распознавание текста (ASR) остаётся за интерфейсом `SpeechRecognizer` (§5.2) и подключается в фазе 2в после S1. Этап 5 (`StageName.MOMENTS`) — чистые правила §6 в `engine/moments/rules.py` над сигналами (речь, события, движение 1b, плохие кадры, смены сцен); группировка моментов разных камер — по смещениям синхронизации. Лента — новый виджет `MomentCard` и событие исполнителя `moment`.

**Tech Stack:** Python 3.12, numpy, onnxruntime (MIT), Silero VAD ONNX (MIT), YAMNet ONNX (Apache-2.0, конвертация tf2onnx), PySide6.

**Spec:** `docs/architecture.md` §4.2 (`audio_event`, `moment`), §5.1 этапы 2 и 5, §5.2, §6, §12; дизайн-система (claude.ai/artifact/CL1yyrtm3XzrovRgnNQRxw): `project/components/MomentCard/README.md`, `project/screens.md` §3 («Уже найдено»). Предыдущий план: `docs/superpowers/plans/2026-11-08-toy-studio-phase2a-import-analysis.md` (его Global Constraints действуют).

**Что не входит (фаза 2в, после S1):** распознавание текста, определение языка участка, ключевые слова тостов и подарков, момент «Подарки» (без текста его не найти), подтяжка границ к фразам, `transcript_excerpt`, критерий «тосты eval ≥ 85 %» (нужны размеченные записи).

## Global Constraints

- Все ограничения плана 2а действуют (Python 3.12, int микросекунды, слои и import-linter, ffmpeg только через `find_tool`, кириллица в путях, `atomic_write`/`*.part`, один тяжёлый исполнитель, прогресс ≤ 4 Гц, тексты через `QCoreApplication.translate`, токены темы, 1280×720 и 125 %).
- Офлайн: модели лежат в `toystudio/engine/models/` (входят в пакет и в установщик), сеть во время анализа не используется. Рядом с каждой моделью — файл лицензии и `MODELS.md` с источником, версией и sha256.
- onnxruntime: только CPU-провайдер в 2б; `intra_op_num_threads` ограничен (`ORT_THREADS = 2`), чтобы этап 2 не отнимал ядра у ffmpeg этапа 1 следующего файла.
- Время сигналов: участки и события хранятся в микросекундах исходного файла; окна YAMNet — 0,96 с с шагом 0,48 с, VAD — кадры 512 сэмплов (32 мс) при 16 кГц.
- Моменты — только из локальных сигналов; в сеть ничего не уходит.
- Типы моментов — `MomentType` из `core/models.py`; бейдж типа нейтральный (`bg-300`), цвет типам не даётся (MomentCard README).
- Пороги правил — модульные константы с комментарием «предварительно, калибруется на размеченных тоях»; правила детерминированы.

## Review Focus

1. **Файл без звука или с тишиной** — этап 2 `skipped`, этап 5 строит только моменты, не требующие звука (их нет — `found = 0`), анализ не падает. Тесты: Task 2 `test_vad_on_silence_returns_no_regions`, Task 6 `test_analyze_no_audio_skips_speech_and_finds_nothing`.
2. **Длинный файл (3 ч)** — память этапа 2 не растёт с длиной: wav читается блоками, результаты копятся компактно. Тест: Task 2 и Task 3 `*_streams_in_blocks` (генератор блоков, пик RSS по `tracemalloc` < 64 МБ на 10 мин синтетики).
3. **Остановка посреди этапа 2 или 5** — этап `interrupted`, при продолжении пересчитывается целиком, без дублей в `audio_event`/`moment`. Тест: Task 6 `test_stop_during_speech_resumes_without_duplicates`.
4. **Один и тот же момент снят двумя камерами** — моменты получают общий `group_id`, лента показывает оба. Тест: Task 5 `test_group_overlapping_moments_across_cameras`.
5. **Повторный анализ после правки порога** (`STAGE_VERSION` поднят) — старые моменты заменяются, ручные отметки ★/⊖ (`mark`) сохраняются у совпадающих моментов. Тест: Task 4 `test_replace_moments_keeps_marks_of_matching_moments`.

---

## File Structure

| Файл | Ответственность |
| --- | --- |
| `toystudio/engine/models/` (`silero_vad.onnx`, `yamnet.onnx`, `yamnet_class_map.csv`, лицензии, `MODELS.md`) | поставляемые модели |
| `tools/fetch_models.py` | скачивание моделей по закреплённым URL и sha256 (разработка и CI) |
| `toystudio/engine/audio/wav.py` | чтение wav 16 кГц блоками |
| `toystudio/engine/audio/vad.py` | Silero VAD → участки речи |
| `toystudio/engine/audio/events.py` | YAMNet → звуковые события |
| `toystudio/core/models.py` (изменить) | `AudioLabel`, `AudioEvent`, `SpeechRegion`, `MomentCandidate`, `Reason` уже есть |
| `toystudio/storage/migrations/0003_moments.sql` | `moment.reasons_json`, `moment.preview`, индекс `audio_event(media_id)` |
| `toystudio/storage/project_store.py` (изменить) | события, участки речи, моменты |
| `toystudio/engine/moments/signals.py` | сбор сигналов файла в посекундные ряды |
| `toystudio/engine/moments/rules.py` | правила §6 |
| `toystudio/engine/moments/group.py` | `group_id` по синхронизации |
| `toystudio/jobs/analyze_job.py` (изменить) | этапы SPEECH и MOMENTS, событие `moment`, превью момента |
| `toystudio/app/widgets/moment_card.py` | карточка момента |
| `toystudio/app/screens/analysis.py`, `viewmodels/analysis_vm.py` (изменить) | лента «Уже найдено», тексты этапов 2 и 5 |
| `toystudio/app/shell.py` (изменить) | уведомление «— найдено N моментов» |

---

### Task 1: Модели и их загрузка

**Files:**
- Create: `tools/fetch_models.py`, `toystudio/engine/models/MODELS.md`, `toystudio/engine/models/LICENSE-silero-vad.txt`, `toystudio/engine/models/LICENSE-yamnet.txt`, `toystudio/engine/models/__init__.py`, `tests/engine/test_models.py`
- Modify: `pyproject.toml` (зависимость `onnxruntime`; `package-data` для `*.onnx`, `*.csv`), `.github/workflows/ci.yml` (шаг `python tools/fetch_models.py` до тестов, с кэшем)

**Interfaces:**
- Produces: `toystudio.engine.models.model_path(name: Literal["silero_vad", "yamnet", "yamnet_class_map"]) -> Path` (`FileNotFoundError` с подсказкой «python tools/fetch_models.py», если файла нет); `tools/fetch_models.py` — `MODELS: dict[str, tuple[url, sha256, filename]]`, `main(argv) -> int` (качает в `*.part`, сверяет sha256, `os.replace`; уже скачанный файл с верным sha256 не качает).

- [ ] **Step 1: Выбрать файлы и закрепить их.** Silero VAD: `silero_vad.onnx` из репозитория `snakers4/silero-vad` (MIT), версия v5 (вход `input` float32 [1, 512], `state` [2, 1, 128], `sr` int64; выход вероятность и новое состояние — сверить с файлом). YAMNet: waveform-вход ONNX-конвертация `audiomagic/yamnet-onnx` на Hugging Face (Apache-2.0, tf2onnx, веса не менялись) и `yamnet_class_map.csv` (521 класс AudioSet) из `tensorflow/models` (`research/audioset/yamnet`). Записать URL, версию, sha256, лицензию и формат входов/выходов в `MODELS.md`. **Скачивание — только с согласия владельца** (контроллер спрашивает до запуска задачи).
- [ ] **Step 2: Тесты** (`tests/engine/test_models.py`, пропуск, если модели не скачаны): `test_model_files_match_pinned_sha256`; `test_silero_runs_on_zeros` (512 нулей → вероятность < 0,1); `test_yamnet_runs_on_one_second` (16 000 нулей → 521 оценка, сумма классов `Silence` максимальна); `test_model_path_hint_when_missing` (подмена каталога → `FileNotFoundError` с текстом про `fetch_models.py`).
- [ ] **Step 3:** FAIL → **Step 4:** реализовать, скачать, PASS. В `.gitignore` модели **не** добавлять: их коммитим (≈ 2 МБ + ≈ 15 МБ), чтобы установщик и офлайн-сборка не зависели от сети; `fetch_models.py` нужен для обновления и проверки sha256 в CI.
- [ ] **Step 5: Commit** `feat(engine): bundled Silero VAD and YAMNet ONNX models`.

### Task 2: Участки речи (Silero VAD)

**Files:**
- Create: `toystudio/engine/audio/__init__.py`, `toystudio/engine/audio/wav.py`, `toystudio/engine/audio/vad.py`, `tests/engine/test_vad.py`
- Modify: `toystudio/core/models.py`

**Interfaces:**
- Consumes: `model_path("silero_vad")` (Task 1); wav 16 кГц моно pcm_s16le от этапа 1.
- Produces:
  - `core.models.SpeechRegion` (frozen): `start_us: int`, `end_us: int`, `prob: float` (средняя вероятность).
  - `engine.audio.wav.read_blocks(wav: Path, block_s: float = 30.0) -> Iterator[tuple[int, np.ndarray]]` — `(start_sample, float32 [-1, 1])`; проверяет 16 кГц моно (`ValueError` иначе).
  - `engine.audio.vad.detect_speech(wav: Path, *, on_progress: Callable[[float], None], should_stop: Callable[[], bool]) -> list[SpeechRegion]` — кадры 512 сэмплов, состояние модели переносится между блоками; порог включения `VAD_ON = 0.5`, выключения `VAD_OFF = 0.35`; минимальная речь `MIN_SPEECH_MS = 250`, склейка пауз `< MERGE_GAP_MS = 300`; `Stopped` из `single_pass` при `should_stop()`.

- [ ] **Step 1: Тесты** (с моделью; синтетика — записанный numpy-генератором wav): `test_vad_on_silence_returns_no_regions`; `test_vad_finds_speech_like_burst` (ряд гласноподобных сигналов: сумма гармоник 120–3000 Гц с огибающей слогов 4 Гц, 5 с, внутри 20 с шума −40 дБ → один участок 5 ± 0,3 с; если синтетика не даёт речи на этой модели — использовать короткий открытый образец речи из `tests/data/` с лицензией CC0 и записать источник); `test_vad_merges_short_pauses`; `test_vad_streams_in_blocks` (10 мин → пик `tracemalloc` < 64 МБ, вызовов `on_progress` > 1); `test_vad_stop` (`should_stop` → `Stopped`).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(engine): speech regions with Silero VAD`.

### Task 3: Звуковые события (YAMNet)

**Files:**
- Create: `toystudio/engine/audio/events.py`, `tests/engine/test_events.py`
- Modify: `toystudio/core/models.py`

**Interfaces:**
- Consumes: `model_path("yamnet")`, `model_path("yamnet_class_map")`, `read_blocks` (Tasks 1–2).
- Produces:
  - `core.models.AudioLabel(StrEnum)`: `SPEECH`, `APPLAUSE`, `MUSIC`, `CHEER`, `LAUGHTER` (значения как в CHECK таблицы `audio_event`).
  - `core.models.AudioEvent` (frozen): `media_id: int`, `start_us: int`, `end_us: int`, `label: AudioLabel`, `score: float`.
  - `engine.audio.events.LABEL_CLASSES: dict[AudioLabel, tuple[str, ...]]` — имена классов AudioSet по `display_name`: SPEECH ← «Speech», «Conversation», «Narration, monologue»; APPLAUSE ← «Applause», «Clapping»; MUSIC ← «Music» и все классы с индексом в диапазоне музыкальных жанров/инструментов карты (собрать по родителю «Music» в онтологии нельзя без онтологии — взять явный список: «Music», «Musical instrument», «Singing», «Pop music», «Folk music», «Dance music», «Electronic music», «Drum», «Accordion», «Guitar», «Keyboard (musical)», «Synthesizer»); CHEER ← «Cheering», «Crowd», «Shout», «Yell»; LAUGHTER ← «Laughter», «Giggle», «Chuckle, chortle». Каждое имя проверяется по `yamnet_class_map.csv` при загрузке модуля (`KeyError` с именем, если класса нет); тест `test_label_classes_exist_in_class_map`.
  - `engine.audio.events.detect_events(media_id: int, wav: Path, *, on_progress, should_stop) -> list[AudioEvent]` — окна 0,96 с с шагом 0,48 с; оценка метки = максимум оценок её классов; метка активна при оценке ≥ `EVENT_ON[label]` (`SPEECH 0.5, APPLAUSE 0.3, MUSIC 0.4, CHEER 0.3, LAUGHTER 0.3`); соседние активные окна одной метки склеиваются (разрыв ≤ 1 окно), события короче `MIN_EVENT_S = 1.0` отбрасываются; `score` — средняя оценка события.
  - `engine.audio.events.music_share(events: list[AudioEvent], start_us: int, end_us: int) -> float` — доля интервала, покрытая MUSIC.

- [ ] **Step 1: Тесты:** `test_events_silence_is_empty`; `test_events_detects_music_like_tone_sequence` (аккорды 220–880 Гц со сменой каждые 0,5 с, 15 с → событие MUSIC ≥ 10 с; если модель не признаёт синтетику музыкой — открытый образец CC0 в `tests/data/` с записанным источником); `test_events_detects_applause_like_noise` (импульсный широкополосный шум с огибающей хлопков 6–10 Гц, 6 с → APPLAUSE; та же оговорка про образец); `test_events_merge_and_min_length` (на подменённых оценках модели — функция склейки отдельно от модели); `test_events_streams_in_blocks`; `test_music_share`.
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(engine): audio events with YAMNet`.

### Task 4: Хранилище событий, речи и моментов

**Files:**
- Create: `toystudio/storage/migrations/0003_moments.sql`, `tests/storage/test_project_store_moments.py`
- Modify: `toystudio/storage/project_store.py`, `toystudio/core/models.py`

**Interfaces:**
- Consumes: `AudioEvent`, `SpeechRegion` (Tasks 2–3); `Moment`, `MomentType`, `Mark`, `Reason` (фаза 1).
- Produces:
  - Миграция: `ALTER TABLE moment ADD COLUMN reasons_json TEXT`, `ADD COLUMN preview TEXT` (путь превью относительно корня проекта), `ADD COLUMN camera_offset_us INTEGER`; `CREATE TABLE speech_region (media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, prob REAL)`; индексы по `media_id` для `audio_event`, `speech_region`, `moment`.
  - `core.models.MomentRow` (frozen): `id: int`, `media_id: int`, `type: MomentType`, `start_us: int`, `end_us: int`, `score: float`, `quality_label: QualityLabel`, `reasons: tuple[Reason, ...]`, `mark: Mark`, `group_id: int | None`, `preview: str | None`.
  - `ProjectStore.replace_audio(media_id, regions: list[SpeechRegion], events: list[AudioEvent]) -> None` (одна транзакция), `speech_regions(media_id) -> list[SpeechRegion]`, `audio_events(media_id) -> list[AudioEvent]`.
  - `ProjectStore.replace_moments(media_id, moments: list[MomentRow]) -> list[int]` — заменяет моменты файла; `mark` переносится с прежнего момента того же типа, пересекающегося по времени ≥ 50 %; возвращает новые id.
  - `ProjectStore.moments(media_id: int | None = None) -> list[MomentRow]`, `set_group_ids(pairs: list[tuple[int, int | None]]) -> None`, `set_mark(moment_id, mark) -> None`, `moment_counts() -> dict[MomentType, int]`.
- [ ] **Step 1: Тесты:** `test_replace_audio_roundtrip_and_idempotent`; `test_replace_moments_keeps_marks_of_matching_moments`; `test_moment_counts`; `test_migration_from_v2_keeps_data`; `test_open_readonly_reads_moments` (R17).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(storage): speech regions, audio events and moments`.

### Task 5: Правила моментов и группировка камер

**Files:**
- Create: `toystudio/engine/moments/__init__.py`, `toystudio/engine/moments/signals.py`, `toystudio/engine/moments/rules.py`, `toystudio/engine/moments/group.py`, `tests/engine/test_moment_rules.py`, `tests/engine/test_moment_group.py`

**Interfaces:**
- Consumes: `SpeechRegion`, `AudioEvent`, `QualityInterval`, смены сцен (`list[int]` мкс), метрики 1b (`motion` 4 к/с из `frames.npz`), `MediaInfo.duration_us`, `sync_offset_us`.
- Produces:
  - `engine.moments.signals.FileSignals` (frozen): `media_id`, `duration_us`, `speech: list[SpeechRegion]`, `events: list[AudioEvent]`, `motion_per_s: np.ndarray` (среднее движение за секунду), `cuts_us: list[int]`, `bad: list[QualityInterval]`; `load_signals(store, cache: Path, media_id) -> FileSignals`.
  - `engine.moments.rules.find_moments(sig: FileSignals) -> list[MomentRow]` (id = 0, mark NONE, group_id None, preview None). Правила (константы модульные, «предварительно»):
    - **Тост:** участки речи, склеенные при паузах ≤ `TOAST_MAX_PAUSE_S = 3.0`, длиной ≥ `TOAST_MIN_S = 25.0`, доля музыки ≤ `TOAST_MAX_MUSIC = 0.3`; оценка `0.5 + 0.2·(аплодисменты или музыка в 10 с после конца) + 0.1·min(1, длина/90 с) + 0.2·(1 − доля музыки)`, максимум 1; причины: «Долгая речь без пауз — 1 мин 12 с», «В конце аплодисменты».
    - **Танец:** события MUSIC ≥ `DANCE_MIN_S = 20.0`, где среднее движение ≥ `DANCE_MOTION = 1.5 ×` медианы движения файла; границы — по границам события музыки; оценка по превышению движения.
    - **Выход молодых:** всплеск APPLAUSE или CHEER ≥ 3 с, начинающийся не позже 5 с после начала MUSIC и в пределах 60 с от начала MUSIC; момент от начала музыки до конца всплеска + 10 с; оценка 0,6 (без слов ведущего — ниже максимума, слова добавит фаза 2в).
    - **Аплодисменты:** APPLAUSE/CHEER ≥ `APPLAUSE_MIN_S = 3.0`, не внутри уже найденного тоста/выхода; оценка по длине.
    - **Музыка:** MUSIC ≥ `MUSIC_MIN_S = 30.0` без танца (среднее движение < порога танца).
    - **Общее:** границы подтягиваются к смене сцены в пределах ±2 с; плохой кадр, покрывающий > 50 % момента, снижает оценку на 0,3 и задаёт `quality_label` (через `overall_label`); пересекающиеся моменты одного типа сливаются; моменты короче 3 с отбрасываются.
  - `engine.moments.group.group_ids(moments: list[MomentRow], offsets_us: dict[int, int | None]) -> list[tuple[int, int | None]]` — моменты разных файлов с известным смещением, пересекающиеся на общей шкале ≥ 50 % меньшего, получают общий `group_id` (наименьший id группы); без смещения — `None`.
- [ ] **Step 1: Тесты** (синтетические сигналы, без моделей): по одному тесту на правило с положительным и отрицательным случаем (`test_toast_found_for_long_speech_ending_in_applause`, `test_toast_rejected_with_music_under_speech`, `test_dance_needs_motion`, `test_entrance_applause_after_music_start`, `test_applause_not_inside_toast`, `test_music_without_dance`, `test_boundaries_snap_to_scene_cut`, `test_bad_frames_lower_score`), `test_no_audio_signals_give_no_moments`, `test_group_overlapping_moments_across_cameras`, `test_no_group_without_offset`.
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(engine): moment rules and camera grouping`.

### Task 6: Этапы 2 и 5 в задании `analyze`

**Files:**
- Modify: `toystudio/jobs/analyze_job.py`, `toystudio/engine/media/thumbs.py`, `tests/jobs/test_analyze_job.py`, `tests/media_fixtures.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - SPEECH: если wav есть — `detect_speech` + `detect_events` → `replace_audio`; `found` = число участков речи; нет wav → `skipped`. Прогресс — секунды звука.
  - MOMENTS: после QUALITY файла — `find_moments(load_signals(...))` → `replace_moments`; превью каждого момента — кадр из середины момента из proxy (`thumbs.frame_at(proxy, at_us, out)`), путь `cache/thumbs/m<moment_id>.jpg`; событие `Event("moment", {"media_id", "moment_id", "type", "start_us", "end_us", "score", "preview"})` на каждый момент; `found` = число моментов.
  - После SYNC — `group_ids(...)` → `set_group_ids` (группировка требует смещений).
  - `STAGE_VERSION[SPEECH] = 1`, `STAGE_VERSION[MOMENTS] = 1`; ETA учитывает этап 2 как `SPEECH_X_RT = 30.0` (уточнить замером в шаге 4 и записать).
- [ ] **Step 1: Тесты:** `test_analyze_finds_toast_and_applause` (клип 40 с: 30 с речеподобного сигнала (или образец CC0) + 5 с аплодисментоподобного; ожидание — тост и аплодисменты; если модели не узнают синтетику — тест на образцах из `tests/data/`); `test_analyze_no_audio_skips_speech_and_finds_nothing`; `test_stop_during_speech_resumes_without_duplicates`; `test_moment_events_sent_with_preview`; `test_moments_grouped_after_sync` (две камеры из `make_noise_clip` + общий всплеск).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS; замерить скорость этапа 2 на 10-мин синтетике, записать в отчёт.
- [ ] **Step 5: Commit** `feat(jobs): speech/audio events and moments stages`.

### Task 7: Карточка момента и живая лента

**Files:**
- Create: `toystudio/app/widgets/moment_card.py`, `tests/app/test_moment_card.py`
- Modify: `toystudio/app/screens/analysis.py`, `toystudio/app/viewmodels/analysis_vm.py`, `toystudio/app/shell.py`, `toystudio/app/i18n/ky.ts`, `toystudio/app/theme/theme.qss.tmpl` (+ генерация), `tests/app/test_analysis_screen.py`

**Interfaces:**
- Consumes: событие `moment` (Task 6), `ProjectStore.open_readonly(...).moments()`.
- Produces:
  - `MomentCard(QFrame)`: `set_data(type: MomentType, start_us, duration_us, quality: QualityLabel, preview: QPixmap | None, reasons: list[str], mark: Mark)`; нейтральный бейдж типа («Тост», «Танец», «Выход молодых», «Подарки», «Аплодисменты», «Музыка»); время в исходнике `format_timecode`, длительность; строка причины (вместо расшифровки до 2в); оценка кадра «чётко / темновато / трясётся». Отметки ★/⊖ в 2б только отображаются (переключение — экран 4, фаза 3).
  - Лента «Уже найдено» (правая колонка экрана 3): моменты по мере прихода событий, новые сверху, не больше 50 карточек (дальше «и ещё N»); при открытии готового проекта — из `moments()`.
  - Тексты этапов: этап 2 «Найдено 12 участков речи» (до 2в), этап 5 «Найдено 14 тостов · 9 танцев · …» (только ненулевые типы, `moment_counts`).
  - Уведомление: «Анализ «{name}» готов — найдено {N} моментов».
- [ ] **Step 1: Тесты:** `test_moment_card_texts_and_neutral_badge`; `test_feed_adds_cards_from_events_newest_first`; `test_feed_caps_at_50`; `test_feed_loaded_from_store_on_reopen`; `test_stage_texts_use_counts`; `test_notification_mentions_moments`; `test_moment_card_retranslates_to_ky`.
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(app): moment card and live «Уже найдено» feed`.

### Task 8: Сквозной тест и документы

**Files:**
- Modify: `tests/app/test_flow_phase2.py`, `docs/architecture.md` (§5.1 этап 2: «распознавание текста — фаза 2в»; §11 лицензии onnxruntime, Silero, YAMNet; §13 строка фазы 2), `docs/testing/phase2a-test-guide.md` → `phase2-test-guide.md` (раздел про ленту моментов), `README.md` (модели и `fetch_models.py`)

- [ ] **Step 1:** Сквозной тест: проект из клипа с тостоподобной речью и аплодисментами → после анализа лента содержит тост и аплодисменты, уведомление с числом моментов, этапы 2 и 5 `done`.
- [ ] **Step 2:** Весь `pytest`, `ruff`, `mypy`, `lint-imports` — PASS.
- [ ] **Step 3: Commit** `docs: phase 2b moments; end-to-end flow with moments`.

---

## Порядок и зависимости

Task 1 → 2 → 3 (модели и звук); 4 (хранилище) после 3; 5 (правила) зависит от типов Tasks 2–4, не от моделей; 6 собирает; 7 — интерфейс; 8 — сквозной тест.

## Критерий выхода фазы 2б

- Тесты зелёные на CI (Windows и Ubuntu), модели проверены по sha256.
- На ноутбуке Windows: этап 2 на 1 ч звука занимает не больше 2 мин (≥ 30× реального времени) при 2 потоках onnxruntime.
- На реальной записи тоя (когда появится) монтажёр видит в ленте тосты и танцы; качество правил меряется в 2в вместе с текстом.
