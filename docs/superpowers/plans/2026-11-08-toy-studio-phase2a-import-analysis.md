# Той Студия — фаза 2а «Импорт и анализ без речи»: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Монтажёр создаёт проект, добавляет видео и музыку, запускает анализ и видит его ход на экранах 1–3. Анализ на компьютере без сети делает копию для анализа, звук, метрики кадра, смены сцен, плохие кадры и синхронизацию камер, переживает закрытие программы и продолжает с места остановки.

**Architecture:** Новая работа ложится в слои фазы 1 (`core` → `engine` / `storage` → `jobs` → `app`, `docs/architecture.md` §3.2). Расчёты без ввода-вывода — в `core` и `engine/analysis` на numpy; ffmpeg вызывается только из `engine/media`. Анализ — задание исполнителя `analyze` с контрольной точкой на каждый этап каждого файла (`stage_run`). Экраны — MVVM без фреймворка (§3.3): виджет подписан на сигналы ViewModel, ViewModel зовёт `JobManager` и хранилище.

**Tech Stack:** Python 3.12, PySide6 ≥ 6.8, numpy, opencv-python-headless, ffmpeg ≥ 7 (отдельный exe), pytest, pytest-qt, comtypes (только Windows).

**Spec:** `docs/architecture.md` (§1.1, §1.3 G1/G4/G7/G9, §4, §5.1, §5.3, §10) + `docs/spikes/phase0-decisions.md` + дизайн-система (claude.ai/artifact/CL1yyrtm3XzrovRgnNQRxw): `project/screens.md` §1–3, §8; компоненты `ProjectCard`, `DropZone`, `AnalysisStage`, `Banner`, `StatusBadge`, `EmptyState`. Код-ориентир — спайк S3: `spikes/s3_decode/single_pass.py`, `metrics.py`, `stats.py`.

**Что не входит (план 2б, после спайка S1):** этап 2 «Слушаем речь и ищем тосты» (VAD, YAMNet, распознавание), этап 5 «Ищем танцы, аплодисменты, музыку» (правила моментов §6), живая лента «Уже найдено», критерий «тосты eval ≥ 85%». В этом плане этапы 2 и 5 показываются в списке со статусом «Появится позже» и пишутся в `stage_run` как `skipped`.

## Global Constraints

- Python `>=3.12,<3.13`; PySide6 `>=6.8`; релиз только Windows 10/11 x64, но `core/`, `engine/`, `storage/`, `jobs/` тесты проходят на macOS и Linux.
- Время в моделях и в БД — `int` микросекунды; `float` секунд только на границе с ffmpeg.
- `core/` не импортирует `PySide6`, `sqlite3`, `subprocess`; `engine/` не импортирует `PySide6`. Правила import-linter из `.importlinter` не ослаблять; добавить контракт «engine без Qt».
- ffmpeg и ffprobe запускаются только списком аргументов (без shell), через `toystudio.engine.media.ffmpeg.find_tool`. Минимальная версия ffmpeg — 7.0 (`-/filter_complex`).
- Пути с кириллицей и пробелами (`D:\Той Студия\Свадьба Айбек и Нурайым\IMG_0412.MP4`) — обычный случай, везде.
- Все записи файлов в `storage/`, `engine/`, `jobs/` — через `atomic_write` или «временный файл `*.part` → `os.replace`».
- Proxy: h264 `libx264 -preset veryfast -crf 26`, `-g` = 1 с, CFR по ближайшей стандартной частоте (`pick_cfr` из S3), **540 по короткой стороне**, поворот вшит, теги BT.709, AAC 48 кГц; HDR → SDR: `zscale+tonemap=hable`, если есть `zscale`, иначе `colorspace` (S3). Звук: wav pcm_s16le 16 кГц моно. Метрики: gray 160×90 (90×160 для портрета), 4 к/с.
- Одновременно работает **один** тяжёлый исполнитель на компьютер (`JobManager(max_parallel=1)`); второй проект встаёт в очередь.
- Прогресс в GUI — не чаще 4 раз в секунду (`PROGRESS_INTERVAL_S = 0.25` уже в `JobManager`).
- Тексты интерфейса: исходник на русском через `QCoreApplication.translate("<Экран>", …)`, перевод в `toystudio/app/i18n/ky.ts`; каждый экран реализует `retranslate()`. Числа — через `core/format.py` (запятая: `11,8 ГБ`, таймкод `03:40:00`).
- Без технических терминов в интерфейсе: коды ошибок и пути логов — только за ссылкой «Подробнее для поддержки».
- Цвета, отступы, радиусы — только из `toystudio/app/theme/generated/tokens.py`.
- Минимальное окно 1280×720; все три экрана проверяются на 1280×720 и 1536×864 (125 %).
- Цели по времени анализа (§1.1): ≤ 30 мин на 3 ч 40 мин 1080p 25/30, ≤ 60 мин для 1080p 50/60, для 4K HEVC — ETA и предупреждение до старта.

## Review Focus

1. **Кириллица и пробелы в пути исходника и проекта** — ffprobe, проверка целостности, single pass и копирование работают. Тест в Task 4: `test_single_pass_cyrillic_paths`; в Task 7: `test_import_copies_cyrillic_path`.
2. **Файл без звуковой дорожки** (телефон в беззвучном режиме, экранная запись) — proxy и метрики есть, wav и огибающей нет, синхронизация его пропускает, анализ не падает. Тесты: Task 4 `test_single_pass_no_audio`, Task 6 `test_sync_skips_media_without_audio`.
3. **Повреждённый или недокопированный файл среди нормальных** — он получает `status='corrupt'`, остальные анализируются, на экране 2 баннер §8 с «Продолжить без него» / «Заменить файл». Тесты: Task 1 `test_integrity_truncated_file`, Task 7 `test_analyze_continues_after_corrupt`, Task 10 `test_corrupt_file_banner`.
4. **Программа закрыта или исполнитель убит посреди этапа** — на диске нет недописанного proxy, этап при следующем открытии `interrupted`, повторный запуск продолжает с него, готовые этапы не пересчитываются. Тесты: Task 4 `test_single_pass_killed_leaves_no_partial`, Task 7 `test_analyze_resumes_after_kill`.
5. **Один и тот же файл добавлен дважды; файл удалён или диск отключён после добавления** — дубль не добавляется (строка «Уже добавлен»), пропавший файл получает `status='missing'`, остальные идут дальше. Тесты: Task 2 `test_add_media_duplicate_returns_existing`, Task 7 `test_analyze_marks_missing_file`.

---

## File Structure

| Файл | Ответственность |
| --- | --- |
| `toystudio/core/models.py` (изменить) | + `MediaInfo`, `MediaKind`, `MediaStatus`, `StageName`, `StageState`, `QualityLabel`, `QualityInterval`, `SyncResult` |
| `toystudio/core/budget.py` | оценка нужного места на диске |
| `toystudio/core/eta.py` | начальная скорость по профилю, EMA, оставшееся время |
| `toystudio/core/format.py` (изменить) | + `format_eta(seconds, lang)` |
| `toystudio/engine/media/ffmpeg.py` | поиск ffmpeg/ffprobe, запуск, версия, наличие `zscale` |
| `toystudio/engine/media/probe.py` | `probe_media`, `check_integrity` |
| `toystudio/engine/media/thumbs.py` | кадр-превью для карточки проекта |
| `toystudio/engine/analysis/metrics.py` | метрики кадра (перенос из S3) + разность гистограмм |
| `toystudio/engine/analysis/single_pass.py` | этап 1 + 1b: один ffmpeg, три выхода, прогресс, остановка |
| `toystudio/engine/analysis/envelope.py` | огибающая звука 100 Гц из wav |
| `toystudio/engine/analysis/scenes.py` | этап 3: смены сцен |
| `toystudio/engine/analysis/quality.py` | этап 4: плохие интервалы и метка качества |
| `toystudio/engine/analysis/sync.py` | синхронизация камер по огибающей |
| `toystudio/storage/migrations/0002_analysis.sql` | новые колонки `media`, таблица `quality_interval` |
| `toystudio/storage/project_store.py` (изменить) | медиа, этапы, результаты анализа, шаг проекта |
| `toystudio/storage/app_store.py` (изменить) | скорости этапов для ETA, статус проекта |
| `toystudio/storage/fs.py` (изменить) | `free_bytes`, `is_removable` |
| `toystudio/jobs/import_job.py` | задание `import`: копирование, проверка, ffprobe |
| `toystudio/jobs/analyze_job.py` | задание `analyze`: этапы по файлам, синхронизация, ETA |
| `toystudio/jobs/worker_main.py` (изменить) | регистрация `import`, `analyze`; аргумент `--stop-file` |
| `toystudio/app/widgets/{status_badge,banner,empty_state,drop_zone,project_card,analysis_stage}.py` | компоненты дизайн-системы |
| `toystudio/app/viewmodels/{projects_vm,new_project_vm,analysis_vm}.py` | ViewModel экранов 1–3 |
| `toystudio/app/screens/{projects,new_project,analysis}.py` | экраны 1–3 |
| `toystudio/app/platform/{taskbar,notify}.py` | прогресс на панели задач (G1), уведомление, трей |
| `toystudio/app/shell.py` (изменить) | навигация между экранами 1–3 |
| `tests/media_fixtures.py` | генерация коротких тестовых клипов ffmpeg |

---

### Task 1: ffmpeg, ffprobe и проверка целостности

**Files:**
- Create: `toystudio/engine/media/__init__.py`, `toystudio/engine/media/ffmpeg.py`, `toystudio/engine/media/probe.py`, `tests/media_fixtures.py`, `tests/engine/test_probe.py`
- Modify: `toystudio/core/models.py`, `pyproject.toml` (зависимости `numpy`, `opencv-python-headless`; `comtypes; sys_platform == 'win32'`), `.importlinter` (контракт `engine-no-qt`)

**Interfaces:**
- Produces:
  - `core.models.MediaKind(StrEnum)`: `VIDEO="video"`, `MUSIC="music"`; `MediaStatus(StrEnum)`: `OK="ok"`, `CORRUPT="corrupt"`, `MISSING="missing"`.
  - `core.models.MediaInfo` (frozen dataclass): `duration_us: int`, `width: int`, `height: int` (отображаемые, после поворота), `rotation: int`, `fps: float`, `codec: str`, `hdr: bool`, `has_audio: bool`, `has_video: bool`, `bit_rate: int`, `creation_time: str | None`.
  - `engine.media.ffmpeg.find_tool(name: Literal["ffmpeg", "ffprobe"]) -> str` — порядок: переменная `TOYSTUDIO_FFMPEG_DIR`, папка `ffmpeg/` рядом с `sys.executable` (сборка PyInstaller), `PATH`; иначе `FileNotFoundError`.
  - `engine.media.ffmpeg.ffmpeg_major() -> int`, `has_filter(name: str) -> bool` (кэшируются).
  - `engine.media.probe.probe_media(path: Path) -> MediaInfo` (`ValueError` для файла без видео и аудио).
  - `engine.media.probe.check_integrity(path: Path, edge_s: float = 2.0) -> bool` — декодирует первые и последние `edge_s` секунд (`-v error … -f null -`, `-sseof -edge_s`); `False`, если код возврата ≠ 0 или в stderr есть строки ошибок.
  - `tests/media_fixtures.make_clip(path: Path, *, duration_s: float = 4.0, size: str = "640x360", fps: int = 25, audio: bool = True, rotate: int = 0, tone_hz: int = 440) -> Path`, `make_truncated(src: Path, dst: Path, keep: float = 0.5) -> Path`, маркер `needs_ffmpeg` (пропуск, если ffmpeg < 7).

- [ ] **Step 1: Тесты** в `tests/engine/test_probe.py`:

```python
@needs_ffmpeg
def test_probe_basic(tmp_path):
    info = probe_media(make_clip(tmp_path / "a.mp4", duration_s=4, size="640x360", fps=25))
    assert info.width == 640 and info.height == 360
    assert abs(info.duration_us - 4_000_000) <= 50_000
    assert info.fps == 25.0 and info.has_audio and info.codec == "h264"

@needs_ffmpeg
def test_probe_rotated_reports_display_size(tmp_path):
    info = probe_media(make_clip(tmp_path / "r.mp4", size="640x360", rotate=90))
    assert (info.width, info.height) == (360, 640) and abs(info.rotation) == 90

@needs_ffmpeg
def test_probe_no_audio(tmp_path):
    assert probe_media(make_clip(tmp_path / "n.mp4", audio=False)).has_audio is False

@needs_ffmpeg
def test_integrity_ok(tmp_path):
    assert check_integrity(make_clip(tmp_path / "a.mp4")) is True

@needs_ffmpeg
def test_integrity_truncated_file(tmp_path):
    src = make_clip(tmp_path / "a.mp4", duration_s=6)
    assert check_integrity(make_truncated(src, tmp_path / "cut.mp4", keep=0.5)) is False

def test_find_tool_env_override(tmp_path, monkeypatch):
    exe = tmp_path / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe.write_text(""); exe.chmod(0o755)
    monkeypatch.setenv("TOYSTUDIO_FFMPEG_DIR", str(tmp_path))
    assert Path(find_tool("ffprobe")) == exe
```

- [ ] **Step 2:** Запустить `pytest tests/engine/test_probe.py -v` — FAIL (модулей нет).
- [ ] **Step 3:** Реализовать типы в `core/models.py` и модули `engine/media/`. `probe_media` разбирает `ffprobe -v error -print_format json -show_format -show_streams`: поворот из `side_data_list[].rotation` или тега `rotate` (как `rotation_of` в S3), HDR — `color_transfer in {"arib-std-b67", "smpte2084"}`, `fps` — `avg_frame_rate` как дробь, `creation_time` — `format.tags.creation_time`. `make_truncated` копирует первые `keep` доли байт.
- [ ] **Step 4:** `pytest tests/engine/test_probe.py -v` — PASS; `lint-imports` — PASS.
- [ ] **Step 5: Commit** `feat(engine): ffmpeg discovery, media probe and integrity check`.

### Task 2: Хранилище: медиа, этапы, результаты анализа

**Files:**
- Create: `toystudio/storage/migrations/0002_analysis.sql`, `tests/storage/test_project_store_analysis.py`
- Modify: `toystudio/storage/project_store.py`, `toystudio/storage/app_store.py`, `toystudio/storage/fs.py`, `toystudio/core/models.py`, `tests/storage/test_fs.py`

**Interfaces:**
- Consumes: `MediaInfo`, `MediaKind`, `MediaStatus` (Task 1).
- Produces:
  - `core.models.StageName(StrEnum)`: `PROBE="probe"`, `PROXY="proxy"` (этап 1 + 1b), `SPEECH="speech"`, `SCENES="scenes"`, `QUALITY="quality"`, `MOMENTS="moments"`; константа `STAGE_ORDER: tuple[StageName, ...]` в этом порядке; `PROJECT_STAGES = (StageName.SYNC,)` с `SYNC="sync"` (этап проекта, `media_id = 0`).
  - `core.models.StageState(StrEnum)`: `PENDING`, `RUNNING`, `DONE`, `FAILED`, `SKIPPED`, `INTERRUPTED` (значения — строчные имена).
  - `core.models.StageRun` (frozen): `media_id: int`, `stage: StageName`, `state: StageState`, `progress: float`, `version: int`, `error_code: str | None`.
  - `core.models.QualityLabel(StrEnum)`: `CLEAR="clear"`, `DARK="dark"`, `SHAKY="shaky"`, `BLURRY="blurry"`; `QualityInterval` (frozen): `media_id: int`, `start_us: int`, `end_us: int`, `label: QualityLabel`.
  - `core.models.MediaRow` (frozen): `id: int`, `kind: MediaKind`, `path: Path`, `copied: bool`, `size: int`, `info: MediaInfo | None`, `status: MediaStatus`, `sync_offset_us: int | None`, `sync_confidence: float | None`.
  - `ProjectStore.add_media(path: Path, kind: MediaKind, size: int, copied: bool) -> tuple[int, bool]` — `(id, created)`; дубль = тот же `resolve()`-путь **или** то же имя файла и размер; дубль возвращает существующий id и `False`.
  - `ProjectStore.set_media_info(media_id: int, info: MediaInfo) -> None`, `set_media_status(media_id: int, status: MediaStatus) -> None`, `set_media_path(media_id: int, path: Path, copied: bool) -> None`, `list_media(kind: MediaKind | None = None) -> list[MediaRow]`, `remove_media(media_id: int) -> None`.
  - `ProjectStore.stage(media_id: int, stage: StageName) -> StageRun | None`, `set_stage(media_id: int, stage: StageName, state: StageState, *, progress: float = 0.0, version: int = 1, error_code: str | None = None) -> None`, `stages() -> list[StageRun]`.
  - `ProjectStore.replace_scene_cuts(media_id: int, cuts_us: list[int], scores: list[float]) -> None`, `scene_cut_count(media_id: int | None = None) -> int`, `replace_quality(media_id: int, intervals: list[QualityInterval]) -> None`, `quality_intervals(media_id: int) -> list[QualityInterval]`, `set_sync(media_id: int, offset_us: int | None, confidence: float | None) -> None`.
  - `ProjectStore.step() -> Literal["files", "analysis", "analysis_done"]` — нет видео → `files`; есть видео и хоть один этап `STAGE_ORDER` не в `done`/`skipped` у файла со `status='ok'` или `sync` не готов → `analysis`; иначе `analysis_done`.
  - `ProjectStore.open()` уже переводит `job.running → interrupted`; добавить то же для `stage_run.state='running'`.
  - `AppStore.speed(stage: str, profile: str) -> float | None`, `set_speed(stage: str, profile: str, x_rt: float) -> None`, `set_status(root: Path, status: str, updated_at: datetime) -> None`; статусы проекта: `"files"`, `"analysis"`, `"analysis_done"`, `"error"`.
  - `storage.fs.free_bytes(path: Path) -> int` (`shutil.disk_usage` ближайшего существующего родителя), `is_removable(path: Path) -> bool` (Windows: `GetDriveTypeW == DRIVE_REMOVABLE`; иначе `False`).

- [ ] **Step 1: Миграция** `0002_analysis.sql`: `ALTER TABLE media ADD COLUMN bit_rate INTEGER`, `has_audio INTEGER`, `has_video INTEGER`, `added_at TEXT`; `CREATE TABLE quality_interval (media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL, label TEXT NOT NULL CHECK (label IN ('dark','shaky','blurry')))`; пересоздать `stage_run` с `CHECK (state IN ('pending','running','done','failed','skipped','interrupted'))` (через новую таблицу + копирование + rename); `CREATE INDEX scene_cut_media ON scene_cut(media_id)`.
- [ ] **Step 2: Тесты** в `tests/storage/test_project_store_analysis.py`:

```python
def test_add_media_duplicate_returns_existing(tmp_path):
    s = new_store(tmp_path)
    f = tmp_path / "IMG_0412.MP4"; f.write_bytes(b"x" * 10)
    a = s.add_media(f, MediaKind.VIDEO, 10, copied=False)
    assert a[1] is True
    assert s.add_media(f, MediaKind.VIDEO, 10, copied=False) == (a[0], False)
    g = tmp_path / "copy" / "IMG_0412.MP4"; g.parent.mkdir(); g.write_bytes(b"x" * 10)
    assert s.add_media(g, MediaKind.VIDEO, 10, copied=False) == (a[0], False)

def test_stage_roundtrip_and_running_becomes_interrupted(tmp_path):
    s = new_store(tmp_path); mid = add_video(s, tmp_path)
    s.set_stage(mid, StageName.PROXY, StageState.RUNNING, progress=0.4)
    s.close()
    s = ProjectStore.open(tmp_path / "p")
    assert s.stage(mid, StageName.PROXY).state is StageState.INTERRUPTED

def test_step_progression(tmp_path):
    s = new_store(tmp_path)
    assert s.step() == "files"
    mid = add_video(s, tmp_path)
    assert s.step() == "analysis"
    for st in STAGE_ORDER:
        s.set_stage(mid, st, StageState.SKIPPED if st in (StageName.SPEECH, StageName.MOMENTS) else StageState.DONE)
    s.set_stage(0, StageName.SYNC, StageState.DONE)
    assert s.step() == "analysis_done"

def test_corrupt_media_does_not_block_step(tmp_path): ...   # 2 видео, одно CORRUPT без этапов → "analysis_done" после этапов второго

def test_migration_from_v1_keeps_data(tmp_path): ...       # БД версии 1 с одной media и stage_run → после open() версия 2, строки на месте

def test_store_cyrillic_media_path(tmp_path): ...           # путь «Той Студия/Свадьба Айбек/IMG 1.MP4» сохраняется и читается обратно
```

  В `tests/storage/test_fs.py`: `test_free_bytes_of_missing_dir_uses_parent`, `test_is_removable_false_on_tmp`.
- [ ] **Step 3:** `pytest tests/storage -v` — FAIL.
- [ ] **Step 4:** Реализовать. `MediaRow.info` собирается из колонок `media`, если `duration_us` не `NULL`.
- [ ] **Step 5:** `pytest tests/storage -v` — PASS.
- [ ] **Step 6: Commit** `feat(storage): media, stage runs and analysis results in project.db`.

### Task 3: Место на диске, ETA и формат оставшегося времени

**Files:**
- Create: `toystudio/core/budget.py`, `toystudio/core/eta.py`, `tests/core/test_budget.py`, `tests/core/test_eta.py`
- Modify: `toystudio/core/format.py`, `tests/core/test_format.py`

**Interfaces:**
- Consumes: `MediaInfo` (Task 1).
- Produces:
  - `core.budget.CACHE_BYTES_PER_MIN = 6_800_000` (proxy ≈ 4,3 МБ/мин по S3 + wav 1,92 МБ/мин + метрики и огибающая, запас 10 %), `EXPORT_RESERVE_BYTES = 2 * 1024**3`.
  - `core.budget.needed_bytes(items: list[tuple[int, int]], copy: bool) -> int` — `items` = `(size_bytes, duration_us)`; `= (Σ size, если copy) + Σ ⌈duration_min⌉ × CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES`.
  - `core.eta.profile_key(info: MediaInfo) -> str` — `"4k_hevc10"` (короткая сторона ≥ 1440 или codec `hevc` и HDR), `"hfr_or_heavy"` (fps > 31 или `bit_rate` > 40 000 000), иначе `"light"`.
  - `core.eta.DEFAULT_X_RT: dict[str, float] = {"light": 8.0, "hfr_or_heavy": 4.0, "4k_hevc10": 0.7}` (§5.3).
  - `core.eta.Eta` — `__init__(self, prior_x_rt: float, alpha: float = 0.2)`; `observe(self, media_s: float, wall_s: float) -> None` (EMA скорости после того, как накоплено ≥ 60 с стены; до этого — `prior_x_rt`); `x_rt` (property); `remaining_s(self, media_left_s: float) -> float`.
  - `core.eta.project_remaining_s(items: list[tuple[str, float]], speeds: dict[str, float]) -> float` — `items` = `(profile_key, media_seconds_left)`.
  - `core.eta.over_target(items: list[tuple[MediaInfo, float]]) -> bool` — `True`, если есть хоть один `4k_hevc10` (для предупреждения до старта).
  - `core.format.format_eta(seconds: float, lang: Literal["ru", "ky"]) -> str`: `< 60 с` → «меньше минуты» / «бир мүнөткө жетпей»; `< 60 мин` → «ещё ~23 мин» / «дагы ~23 мүн»; иначе «ещё ~1 ч 10 мин» / «дагы ~1 с 10 мүн»; минуты округляются вверх.

- [ ] **Step 1: Тесты:**

```python
def test_needed_bytes_copy_and_cache():
    gib = 1024**3
    items = [(6 * gib, 110 * 60 * US_PER_S), (int(5.8 * gib), 110 * 60 * US_PER_S)]
    assert needed_bytes(items, copy=False) == 220 * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES
    assert needed_bytes(items, copy=True) == 6 * gib + int(5.8 * gib) + 220 * CACHE_BYTES_PER_MIN + EXPORT_RESERVE_BYTES

def test_profile_keys():
    assert profile_key(info(1920, 1080, 25.0, "h264", 20_000_000)) == "light"
    assert profile_key(info(1920, 1080, 50.0, "h264", 28_000_000)) == "hfr_or_heavy"
    assert profile_key(info(1920, 1080, 25.0, "h264", 80_000_000)) == "hfr_or_heavy"
    assert profile_key(info(2160, 3840, 30.0, "hevc", 45_000_000, hdr=True)) == "4k_hevc10"

def test_eta_uses_prior_for_first_minute_then_ema():
    e = Eta(prior_x_rt=8.0)
    e.observe(media_s=300, wall_s=30)          # 10x, но < 60 с стены
    assert e.x_rt == 8.0
    e.observe(media_s=300, wall_s=40)          # итого 70 с стены → EMA начинается
    assert 8.0 < e.x_rt < 10.0
    assert e.remaining_s(media_left_s=600) == pytest.approx(600 / e.x_rt)

def test_format_eta():
    assert format_eta(30, "ru") == "меньше минуты"
    assert format_eta(22 * 60 + 1, "ru") == "ещё ~23 мин"
    assert format_eta(70 * 60, "ru") == "ещё ~1 ч 10 мин"
    assert format_eta(22 * 60 + 1, "ky") == "дагы ~23 мүн"
```

- [ ] **Step 2:** `pytest tests/core -v` — FAIL.
- [ ] **Step 3:** Реализовать (чистый Python, mypy strict).
- [ ] **Step 4:** `pytest tests/core -v` и `mypy toystudio/core` — PASS.
- [ ] **Step 5: Commit** `feat(core): disk budget and analysis ETA`.

### Task 4: Этап 1 + 1b — один проход декодирования

**Files:**
- Create: `toystudio/engine/analysis/__init__.py`, `toystudio/engine/analysis/metrics.py`, `toystudio/engine/analysis/single_pass.py`, `toystudio/engine/analysis/envelope.py`, `tests/engine/test_metrics.py`, `tests/engine/test_single_pass.py`

**Interfaces:**
- Consumes: `find_tool`, `has_filter`, `MediaInfo` (Task 1).
- Produces:
  - `engine.analysis.metrics.METRIC_KEYS = ("sharpness", "brightness", "motion", "shake", "histdiff")`; `MetricsAccumulator(fps: float = 4.0)` с `update(chunk: np.ndarray) -> None`, `result() -> dict[str, np.ndarray]` (+ ключ `t` в секундах); `FrameReader` — перенос из `spikes/s3_decode/metrics.py` без изменений поведения. `histdiff` — хи-квадрат между нормированными 32-бинными гистограммами яркости соседних кадров (первый кадр — 0).
  - `engine.analysis.single_pass.Outputs` (frozen): `proxy: Path`, `wav: Path`, `frames: Path` (`.npz`), `envelope: Path` (`.npy`).
  - `engine.analysis.single_pass.outputs_for(cache: Path, media_id: int) -> Outputs` — `cache/proxy/<id>.mp4`, `cache/audio/<id>.wav`, `cache/frames/<id>.npz`, `cache/wave/<id>.npy`.
  - `engine.analysis.single_pass.build_command(src: Path, info: MediaInfo, out: Outputs, *, tonemap: Literal["zscale", "fallback"]) -> list[str]` — граф как в S3 (`build_filter_graph`), выходы пишутся в `*.part`-пути; метрики в `pipe:1`; `-progress pipe:2`.
  - `engine.analysis.single_pass.run(src: Path, info: MediaInfo, out: Outputs, *, on_progress: Callable[[float], None], should_stop: Callable[[], bool]) -> dict[str, np.ndarray]` — читает pipe через `FrameReader`, вызывает `on_progress(media_seconds_done)` не чаще 4 раз в секунду, при `should_stop()` убивает ffmpeg и бросает `Stopped`; при успехе: `np.savez` метрик, огибающая из wav, затем `os.replace` всех `*.part` (proxy последним). При любой ошибке `*.part` удаляются. Без звука в исходнике: wav и огибающая не создаются, граф без аудиовыхода.
  - `engine.analysis.single_pass.Stopped(Exception)`, `FfmpegFailed(Exception)` с полем `stderr_tail: str`.
  - `engine.analysis.envelope.envelope_from_wav(wav: Path, rate_hz: int = 100) -> np.ndarray` — RMS по окнам 10 мс, `float32`.

- [ ] **Step 1: Тесты метрик** (`tests/engine/test_metrics.py`): перенести `tests/spikes/test_s3_metrics.py` на новый модуль; добавить `test_histdiff_spikes_on_content_change` — 8 кадров одного шума, потом 8 кадров другого распределения: максимум `histdiff` на индексе 8, остальные < 0,1 от него.
- [ ] **Step 2: Тесты прохода** (`tests/engine/test_single_pass.py`, все `@needs_ffmpeg`):

```python
def test_single_pass_outputs(tmp_path):
    src = make_clip(tmp_path / "a.mp4", duration_s=4, size="1280x720", fps=25)
    out = outputs_for(tmp_path / "cache", 1)
    m = run(src, probe_media(src), out, on_progress=lambda s: None, should_stop=lambda: False)
    p = probe_media(out.proxy)
    assert (p.width, p.height) == (960, 540) and p.fps == 25.0
    assert 15 <= len(m["sharpness"]) <= 17            # 4 к/с × 4 с
    assert out.wav.exists() and out.envelope.exists() and out.frames.exists()
    assert not list((tmp_path / "cache").rglob("*.part"))

def test_single_pass_rotated_portrait(tmp_path):    # rotate=90 → proxy 540×960, rotation 0
def test_single_pass_no_audio(tmp_path):            # audio=False → proxy и frames есть, wav/envelope нет
def test_single_pass_cyrillic_paths(tmp_path):      # src и cache внутри «Той Студия/Свадьба Айбек и Нурайым»
def test_single_pass_killed_leaves_no_partial(tmp_path):
    src = make_clip(tmp_path / "a.mp4", duration_s=20)
    out = outputs_for(tmp_path / "cache", 1)
    calls = []
    with pytest.raises(Stopped):
        run(src, probe_media(src), out, on_progress=calls.append, should_stop=lambda: len(calls) > 0)
    assert not out.proxy.exists() and not list((tmp_path / "cache").rglob("*.part"))
def test_progress_monotonic_and_reaches_duration(tmp_path): ...
```

- [ ] **Step 3:** `pytest tests/engine -v` — FAIL.
- [ ] **Step 4:** Реализовать, переиспользуя из S3 `build_filter_graph`, `pick_cfr`, разбор `-progress`. Код спайка не импортировать — перенести в `engine/analysis/` и упростить (без замеров CPU/RSS, без режимов `drain`/`none`).
- [ ] **Step 5:** `pytest tests/engine -v` — PASS.
- [ ] **Step 6: Commit** `feat(engine): single-pass proxy, audio and frame metrics`.

### Task 5: Этапы 3 и 4 — смены сцен и плохие кадры

**Files:**
- Create: `toystudio/engine/analysis/scenes.py`, `toystudio/engine/analysis/quality.py`, `tests/engine/test_scenes.py`, `tests/engine/test_quality.py`

**Interfaces:**
- Consumes: словарь метрик из `single_pass.run` / `np.load(frames.npz)` (Task 4); `QualityInterval`, `QualityLabel` (Task 2).
- Produces:
  - `engine.analysis.scenes.detect_cuts(m: Mapping[str, np.ndarray], fps: float = 4.0) -> list[tuple[int, float]]` — `(at_us, score)`. Рез: `histdiff[i] > median + 6 × MAD` **и** `histdiff[i] > 0,25` **и** `motion[i] > 2 × median(motion)`; между резами ≥ 1 с (оставлять больший `score`); `score = histdiff[i] / max(histdiff)`. Константы — модульные (`CUT_K = 6.0`, `CUT_FLOOR = 0.25`, `MIN_GAP_S = 1.0`).
  - `engine.analysis.quality.bad_intervals(media_id: int, m: Mapping[str, np.ndarray], fps: float = 4.0) -> list[QualityInterval]`. Предварительные пороги (калибруются в 2б на реальных тоях, вынести константами): `DARK_LUMA = 40.0` (средняя яркость 0–255), `BLUR_REL = 0.35` (резкость ниже 0,35 × медианы файла), `SHAKE_PX = 3.0` (сдвиг фазовой корреляции на кадре 160 px), минимальная длина интервала `MIN_BAD_S = 1.0`. Приоритет при пересечении: `dark` > `blurry` > `shaky`; соседние интервалы одной метки с разрывом < 0,5 с склеиваются.
  - `engine.analysis.quality.overall_label(intervals: list[QualityInterval], duration_us: int) -> QualityLabel` — метка, покрывающая > 30 % длительности, иначе `CLEAR`.

- [ ] **Step 1: Тесты** на синтетических массивах (без ffmpeg):

```python
def metrics(n, **over):  # n кадров, по умолчанию «нормальная» картинка
    base = {"sharpness": np.full(n, 100.0), "brightness": np.full(n, 120.0),
            "motion": np.full(n, 2.0), "shake": np.full(n, 0.5), "histdiff": np.full(n, 0.02)}
    base.update(over); return base

def test_detect_single_cut():
    h = np.full(80, 0.02); h[40] = 0.9
    mo = np.full(80, 2.0); mo[40] = 30.0
    cuts = detect_cuts(metrics(80, histdiff=h, motion=mo))
    assert [c[0] for c in cuts] == [10_000_000]

def test_flash_without_motion_is_not_cut(): ...        # histdiff всплеск без motion → []
def test_cuts_closer_than_gap_keep_stronger(): ...      # всплески на 40 и 42 (0,5 с) → один рез на большем
def test_dark_interval(): ...                           # brightness 20 на кадрах 8..20 → один DARK 2,0–5,25 с
def test_short_dark_ignored(): ...                      # 3 кадра (0,75 с) → []
def test_blurry_relative_to_median(): ...
def test_overall_label_dark_when_mostly_dark(): ...
```

- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(engine): scene cuts and bad-frame intervals`.

### Task 6: Синхронизация камер по звуку

**Files:**
- Create: `toystudio/engine/analysis/sync.py`, `tests/engine/test_sync.py`
- Modify: `toystudio/core/models.py` (`SyncResult`)

**Interfaces:**
- Consumes: огибающие `cache/wave/<id>.npy` (Task 4), `MediaRow` (Task 2).
- Produces:
  - `core.models.SyncResult` (frozen): `media_id: int`, `offset_us: int | None` (позиция начала файла на шкале опорного файла), `confidence: float`.
  - `engine.analysis.sync.estimate_offset(ref: np.ndarray, other: np.ndarray, rate_hz: int = 100) -> tuple[int, float]` — кросс-корреляция через FFT по `log1p`-огибающим после вычитания скользящего среднего 1 с и нормировки; `offset_us`, `confidence = (peak − второй пик вне ±0,5 с) / std корреляции`.
  - `engine.analysis.sync.sync_project(envelopes: dict[int, np.ndarray], rate_hz: int = 100, min_confidence: float = 6.0, min_overlap_s: float = 30.0) -> list[SyncResult]` — опорный = самый длинный; для файлов без огибающей и при `confidence < min_confidence` или перекрытии < `min_overlap_s` → `offset_us=None`. Опорный получает `offset_us=0`, `confidence=inf`.

- [ ] **Step 1: Тесты:**

```python
def noise_env(n, seed): ...   # огибающая «речи и музыки»: шум с огибающими всплесками, 100 Гц

def test_offset_found_within_10ms():
    base = noise_env(60_00, 1)                     # 60 с
    other = base[1234:1234 + 30_00]                # кусок с 12,34 с длиной 30 с
    off, conf = estimate_offset(base, other)
    assert abs(off - 12_340_000) <= 10_000 and conf >= 6.0

def test_unrelated_audio_low_confidence():
    off, conf = estimate_offset(noise_env(6000, 1), noise_env(3000, 2))
    assert conf < 6.0

def test_sync_skips_media_without_audio():
    res = {r.media_id: r for r in sync_project({1: noise_env(6000, 1), 2: noise_env(6000, 1)[500:3800], 3: None})}
    assert res[1].offset_us == 0 and abs(res[2].offset_us - 5_000_000) <= 10_000 and res[3].offset_us is None

def test_short_overlap_rejected(): ...            # перекрытие 10 с → None
def test_other_camera_noise_and_gain(): ...       # кусок × 0,3 + шум 20 % → смещение в пределах 20 мс
```

- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(engine): camera sync by audio envelope`.

### Task 7: Задания исполнителя `import` и `analyze`

**Files:**
- Create: `toystudio/jobs/import_job.py`, `toystudio/jobs/analyze_job.py`, `toystudio/engine/media/thumbs.py`, `tests/jobs/test_import_job.py`, `tests/jobs/test_analyze_job.py`
- Modify: `toystudio/jobs/worker_main.py`, `toystudio/jobs/protocol.py` (только если нужен новый тип — предпочтительно обойтись `Event`)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `worker_main` принимает `--stop-file PATH`; `JobContext.should_stop() -> bool` = файл существует. `JobManager` не меняется: «Остановить» в GUI создаёт stop-файл, затем через 10 с вызывает `cancel()`.
  - `JOB_KINDS["import"]` (`import_job.run(ctx)`): для каждой `media` со `status='ok'` без `info`: если в `project.db` стоит флаг копирования (`media.copied = 1`, путь ещё вне проекта) — копирует в `<project>/media/<имя>` блоками по 8 МБ через `*.part` → `os.replace`, событие `Event("copy", {"media_id", "done_bytes", "total_bytes"})`; затем `check_integrity` → при `False` `status='corrupt'`, `Event("media", {"media_id", "status": "corrupt"})`; иначе `probe_media` → `set_media_info`, `Event("media", {"media_id", "status": "ok", "duration_us"})`. Отсутствующий файл → `status='missing'`.
  - `JOB_KINDS["analyze"]` (`analyze_job.run(ctx)`): `STAGE_VERSION: dict[StageName, int]` (все `1`). Для каждого видео со `status='ok'` по порядку `STAGE_ORDER`: этап с `state=done` и той же версией пропускается; `SPEECH` и `MOMENTS` → `skipped`; `PROBE` — `check_integrity` + `probe_media` (если не сделано импортом); `PROXY` → `single_pass.run`; `SCENES` → `detect_cuts` → `replace_scene_cuts`; `QUALITY` → `bad_intervals` → `replace_quality`. Перед стартом этапа файл проверяется на существование (`missing` → файл пропускается). После всех файлов — `SYNC` (`media_id=0`): `sync_project` → `set_sync` у каждого.
  - События анализа: `Progress(stage=<StageName>, done=<media_s>, total=<media_s>, media_id=str(id))`; `Event("stage", {"media_id", "stage", "state", "found": int | None})` (`found` = число резов для `scenes`, число плохих интервалов для `quality`); `Event("eta", {"remaining_s": float})` не чаще раза в 2 с; при `Stopped` — этап `interrupted`, `Done()` не шлётся, выход с `Failed("stopped", "")`.
  - `engine.media.thumbs.card_thumbnail(proxy: Path, out: Path) -> Path` — кадр из середины proxy (`ffmpeg -ss <середина> -i proxy -frames:v 1`, JPEG через `*.part`); `analyze_job` вызывает её после `PROXY` первого видео и пишет `cache/thumbs/card.jpg`.
  - После каждого `PROXY` — `AppStore.set_speed("proxy", profile_key(info), x_rt)` (EMA хранится в `app.db`; путь к `app.db` передаётся `--app-db`).

- [ ] **Step 1: Тесты** (`@needs_ffmpeg`, исполнитель запускается как подпроцесс `python -m toystudio.jobs.worker_main`, как в `tests/jobs/test_manager.py`):

```python
def test_import_copies_cyrillic_path(tmp_path): ...
    # проект в «Той Студия/Свадьба Айбек и Нурайым», copied=True → файл в media/, info заполнена, событие copy дошло до 100 %

def test_import_marks_corrupt_and_missing(tmp_path): ...

def test_analyze_two_files_and_sync(tmp_path):
    # два клипа 8 с с одним звуком, второй — кусок первого со сдвигом 2 с (make_clip поверх общего wav)
    s = run_worker("analyze", project)
    assert all(st.state in (StageState.DONE, StageState.SKIPPED) for st in s.stages())
    rows = {m.id: m for m in s.list_media()}
    assert abs(rows[2].sync_offset_us - 2_000_000) <= 20_000
    assert (project / "cache" / "proxy" / "1.mp4").exists()

def test_analyze_continues_after_corrupt(tmp_path): ...
def test_analyze_marks_missing_file(tmp_path): ...
def test_analyze_resumes_after_kill(tmp_path):
    # 2 файла; убить процесс после первого Event("stage", stage="proxy", state="done");
    # открыть ProjectStore → этап второго файла interrupted/pending, первого — done;
    # перезапуск analyze → proxy первого файла не пересоздаётся (mtime тот же), всё done
def test_analyze_stop_file_interrupts_stage(tmp_path): ...
```

- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS; `lint-imports` — PASS (jobs → engine/storage/core разрешено).
- [ ] **Step 5: Commit** `feat(jobs): import and analyze worker jobs with per-stage checkpoints`.

### Task 8: Компоненты дизайн-системы

**Files:**
- Create: `toystudio/app/widgets/status_badge.py`, `banner.py`, `empty_state.py`, `drop_zone.py`, `project_card.py`, `analysis_stage.py`, `tests/app/test_widgets_phase2.py`

**Interfaces:**
- Consumes: `tokens.py`, `ElidedLabel`/`ElidedButton` (фаза 1), `core.format`.
- Produces:
  - `StatusBadge(QWidget)`: `set_status(kind: Literal["analysis", "ready", "exported", "error", "offline"], percent: int | None = None)`; всегда иконка + слово (таблица `StatusBadge/README.md`): «Анализ · 64%», «Черновик готов», «Экспортирован», «Ошибка», «Нет интернета».
  - `Banner(QFrame)`: `__init__(kind: Literal["error", "offline", "info"])`; `set_content(title: str, body: str, actions: list[tuple[str, str]], support_detail: str | None = None)`; сигнал `action(str)` (ключ кнопки); ссылка «Подробнее для поддержки» показывается только при `support_detail` и раскрывает текст по клику.
  - `EmptyState(QWidget)`: `__init__(icon: str, title: str, body: str, action: str | None)`; сигнал `activated()`.
  - `DropZone(QFrame)`: `__init__(kind: MediaKind)`; принимает перетаскивание файлов и папок (папка → файлы с расширениями `VIDEO_EXT = {".mp4", ".mov", ".mts", ".m2ts", ".mkv", ".avi"}` / `MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}` рекурсивно); сигнал `files_dropped(list[Path])`; кнопка «Выбрать файлы»; `set_rows(rows: list[FileRow])` превращает зону в список с суммой «6 файлов · 11,8 ГБ · 3 ч 40 мин»; `FileRow` (frozen): `media_id`, `name`, `size`, `duration_us | None`, `state: Literal["copying", "checking", "ready", "corrupt", "missing", "duplicate"]`, `percent: int | None`. Тексты состояний: «Копируем… 42%», «Проверяем…», «Готово ✓», «Файл повреждён !» (+ кнопка «Заменить»), «Файл не найден», «Уже добавлен». Подсветка при наведении — `garnet-500` / `garnet-tint`.
  - `ProjectCard(QFrame)`: `set_data(name: str, kind_text: str, date_text: str, duration_us: int | None, status: str, percent: int | None, eta_text: str | None, preview: QPixmap | None)`; сигналы `opened()`, `context_requested(QPoint)`. Превью 16:9 на `player-bg`, название ≤ 2 строк с `…`, строка «Анализ · 64% · ещё ~12 мин».
  - `AnalysisStage(QFrame)`: `set_state(state: Literal["waiting", "running", "done", "error", "later"], found_text: str | None = None, percent: float | None = None)`; `later` — серый текст «Появится позже» (этапы 2 и 5 в этой фазе).

- [ ] **Step 1: Тесты** (pytest-qt, offscreen): `test_status_badge_has_icon_and_word` (для каждого статуса текст непустой и иконка задана); `test_banner_support_link_hidden_without_detail`; `test_drop_zone_accepts_folder_recursively` (папка с `a.MP4`, `b.txt`, `sub/c.mov` → `files_dropped` с двумя путями); `test_drop_zone_summary_text` (`FileRow` на 6,0 и 5,8 ГиБ, 110 + 110 мин → «2 файла · 11,8 ГБ · 03:40:00»); `test_project_card_elides_long_name` (название 200 символов → 2 строки и «…»); `test_widgets_retranslate_to_ky` (после `install_language("ky")` тексты меняются).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(app): status badge, banner, empty state, drop zone, project card, analysis stage`.

### Task 9: Экран 1 «Проекты»

**Files:**
- Create: `toystudio/app/viewmodels/__init__.py`, `toystudio/app/viewmodels/projects_vm.py`, `toystudio/app/screens/__init__.py`, `toystudio/app/screens/projects.py`, `tests/app/test_projects_screen.py`

**Interfaces:**
- Consumes: `AppStore.list_projects`, `ProjectStore.open/step`, `ProjectCard`, `EmptyState` (Tasks 2, 8).
- Produces:
  - `ProjectsVM(QObject)`: `__init__(app_store: AppStore)`; `reload() -> None`; `items: list[ProjectItem]` (`root`, `name`, `kind`, `event_date`, `status`, `percent`, `eta_s`, `duration_us`, `preview_path`); сигналы `changed()`, `open_requested(Path, str)` (корень и шаг из `ProjectStore.step()`), `new_requested(list[Path])` (файлы, брошенные на окно); `set_progress(root: Path, percent: int, eta_s: float | None)`; `search(text: str)`, `filter_status(status: str | None)`.
  - `ProjectsScreen(QWidget)`: сетка карточек (4 в ряд при ширине ≥ 1800 px, 3 при ≥ 1350, иначе 2), заголовок «Проекты», поиск, фильтр статуса, primary «Новый проект» — единственная гранатовая кнопка. Пустое состояние §1 («Смонтируем первый той» / «Перетащите сюда папку с видео или создайте проект»); всё окно — зона перетаскивания. Правый клик по карточке: «Переименовать», «Открыть папку», «Удалить проект (видео не удаляется)» — удаление спрашивает подтверждение и удаляет только папку проекта при `copied=0`; при `copied=1` подтверждение называет, что копии видео в папке проекта тоже удалятся.
  - Превью карточки — `cache/thumbs/card.jpg` (делает Task 7); нет файла — пустое превью на `player-bg`.

- [ ] **Step 1: Тесты:** `test_empty_state_when_no_projects`; `test_cards_sorted_analysis_first` (3 проекта, один `analysis` с более старой датой → первый); `test_drop_folder_on_empty_window_emits_new_requested`; `test_card_click_opens_saved_step` (проект с `step()=="analysis"` → `open_requested(root, "analysis")`); `test_delete_project_keeps_source_video` (исходник вне папки проекта остаётся); `test_grid_columns_at_1280_and_1920`.
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(app): projects screen`.

### Task 10: Экран 2 «Создание проекта и загрузка файлов»

**Files:**
- Create: `toystudio/app/viewmodels/new_project_vm.py`, `toystudio/app/screens/new_project.py`, `tests/app/test_new_project_screen.py`

**Interfaces:**
- Consumes: `ProjectStore.create/add_media/list_media`, `needed_bytes`, `free_bytes`, `is_removable`, `over_target`, `JobManager`, `DropZone`, `Banner` (Tasks 2, 3, 7, 8).
- Produces:
  - `NewProjectVM(QObject)`: `__init__(app_store: AppStore, jobs: JobManager, projects_dir: Path)`; поля `name`, `event_date: date`, `kind: ToiKind`, `copy: bool`; `add_files(kind: MediaKind, paths: list[Path]) -> None` (создаёт проект при первом файле в `projects_dir/<name или «Новый проект N»>`, регистрирует в `AppStore`, добавляет `media`, запускает задание `import` с `job_id = f"import:{root}"`; дубль → строка `duplicate`); `remove_file(media_id)`, `replace_file(media_id, path)`; `disk_check() -> DiskCheck` (`free`, `needed`, `drive`, `ok`); `can_start: bool` (есть хоть одно видео со `status='ok'` и проверенное, импорт завершён, `disk_check().ok`); `start_analysis() -> None` (запускает `analyze`, сигнал `analysis_started(Path)`); сигналы `rows_changed(MediaKind, list[FileRow])`, `disk_changed(DiskCheck)`, `banner(str | None)`, `can_start_changed(bool)`, `warning_4k(bool)`.
  - Дата по умолчанию — `creation_time` первого видео после ffprobe, иначе сегодня. Копирование по умолчанию выключено; если любой добавленный файл на съёмном диске (`is_removable`) — переключатель включается и рядом подпись «Файлы на флешке — лучше скопировать».
  - Строка места: «На диске D: свободно 48 ГБ, нужно ~16 ГБ» (на macOS/Linux вместо буквы — имя тома или «На диске»). При нехватке — баннер §8 «На диске D: не хватает места — нужно ещё 7,2 ГБ.» / «Освободите место или выберите другую папку для копии.» с «Выбрать папку», «Проверить снова»; «Начать анализ» неактивна.
  - Повреждённый файл — баннер §8 «Файл IMG_0419.MP4 не читается — возможно, он повреждён или скопирован не до конца.» / «Остальные файлы проанализируем без него.» с «Продолжить без него», «Заменить файл».
  - 4K HEVC среди файлов (`over_target`) — инфо-баннер: «Видео 4K анализируется долго — около {format_eta} на этом компьютере. Можно уйти: мы пришлём уведомление.»
  - `NewProjectScreen(QWidget)`: две колонки 40/60 по §2: слева «Название» (плейсхолдер «Свадьба Айбек и Нурайым»), «Дата», «Тип» чипами (Свадьба · Той · Юбилей · Беш-бармак · Сүннөт той · Кыз узатуу · Другое); справа `DropZone` «Видео» (60 % высоты) и «Музыка»; внизу справа primary «Начать анализ» 40 px.

- [ ] **Step 1: Тесты** (VM с фиктивным `JobManager`, исполнитель не запускается; экран — pytest-qt): `test_start_disabled_without_video`; `test_duplicate_file_row`; `test_disk_shortage_banner_and_disabled_start` (подменить `free_bytes`); `test_removable_turns_copy_on` (подменить `is_removable`); `test_corrupt_file_banner` (событие `media` со статусом `corrupt` → баннер с двумя действиями, «Продолжить без него» убирает файл из проверки `can_start`); `test_default_date_from_creation_time`; `test_4k_warning_banner`; `test_layout_fits_1280x720` (все элементы видимы, нет обрезанных кнопок).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(app): new project screen with file import and disk check`.

### Task 11: Экран 3 «Прогресс анализа», уведомление и панель задач

**Files:**
- Create: `toystudio/app/viewmodels/analysis_vm.py`, `toystudio/app/screens/analysis.py`, `toystudio/app/platform/__init__.py`, `toystudio/app/platform/taskbar.py`, `toystudio/app/platform/notify.py`, `tests/app/test_analysis_screen.py`

**Interfaces:**
- Consumes: `JobManager` сигналы, события Task 7, `AnalysisStage`, `Banner`, `format_eta`, `project_remaining_s`.
- Produces:
  - `AnalysisVM(QObject)`: `__init__(root: Path, jobs: JobManager, app_store: AppStore)`; `stages: list[StageView]` (5 этапов §5.1 по порядку с текстами из `AnalysisStage/README.md`; `state`, `found_text`, `percent`); `overall_percent: float` (доля медиа-секунд всех файлов по этапам `PROBE`, `PROXY`, `SCENES`, `QUALITY` + 1 % на `SYNC`); `eta_text: str | None`; сигналы `changed()`, `finished(int)` (число найденных смен сцен — в 2б заменится на моменты), `failed(str, str)`; `stop() -> None` (stop-файл, затем `cancel` через 10 с); `resume() -> None` (повторный `analyze`).
  - Тексты «что найдено»: этап 1 — «Готово: 6 файлов», этап 3 — «Найдено 128 смен сцен», этап 4 — «Чётко: 4 файла · темновато: 1 · трясётся: 1» (по `overall_label`); этапы 2 и 5 — `later`.
  - `AnalysisScreen(QWidget)`: слева 60 % — общий бар, `timecode-lg` «ещё ~23 мин», `caption` «Анализ идёт на этом компьютере, интернет не нужен», список из 5 `AnalysisStage`; справа 40 % — «Уже найдено» с `EmptyState` «Моменты появятся после поиска тостов и танцев» (до 2б). Внизу: чекбокс с колокольчиком «Уведомить, когда закончится» (включён), «Свернуть», ghost «Остановить». После остановки — кнопка «Продолжить анализ» и строка «Пройденные этапы сохранены».
  - `platform.taskbar.TaskbarProgress`: `__init__(window: QWidget)`; `set_progress(percent: float | None)`; на Windows — COM `ITaskbarList3` через `comtypes` (`SetProgressState`/`SetProgressValue`), на других ОС и при любой ошибке COM — молча ничего не делает (G1).
  - `platform.notify.Notifier`: `__init__(icon: QIcon)`; `notify(title: str, body: str, on_click: Callable[[], None]) -> None` через `QSystemTrayIcon.showMessage`; `minimize_to_tray(window: QWidget) -> None`. Текст по завершении: заголовок «Той Студия», тело «Анализ «{name}» готов» (в 2б добавится «— найдено N моментов»). Клик открывает проект (шаг «Задание» появится в фазе 3; пока — этот экран в состоянии «готово»).

- [ ] **Step 1: Тесты:** `test_stage_texts_and_later_states` (этапы 2 и 5 — `later`); `test_progress_events_update_overall_and_eta` (поток `Progress`/`Event("eta")` → процент растёт, текст «ещё ~N мин»); `test_stop_creates_stop_file_and_shows_resume`; `test_finished_notifies_when_checked` (подмена `Notifier`); `test_no_notification_when_unchecked`; `test_taskbar_noop_off_windows`; `test_reopen_interrupted_project_shows_resume` (в БД этап `interrupted` → кнопка «Продолжить анализ»).
- [ ] **Step 2:** FAIL → **Step 3:** реализовать → **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(app): analysis progress screen, notification and taskbar progress`.

### Task 12: Навигация, перевод и сквозной тест

**Files:**
- Modify: `toystudio/app/shell.py`, `toystudio/app/main.py`, `toystudio/app/i18n/ky.ts` (+ пересборка `ky.qm`), `README.md` (раздел «Запуск»: ffmpeg ≥ 7 в `PATH` или `TOYSTUDIO_FFMPEG_DIR`)
- Create: `tests/app/test_flow_phase2.py`

**Interfaces:**
- Consumes: экраны и VM Tasks 9–11.
- Produces:
  - `Shell` держит `QStackedWidget` с тремя экранами и `show_projects()`, `show_new_project(files: list[Path] | None = None)`, `show_analysis(root: Path)`; верхняя панель — степпер «Файлы → Анализ → Задание → Редактор → Экспорт», шаги после «Анализ» неактивны до фаз 3–5. Один `JobManager(max_parallel=1)` на приложение; прогресс анализа идёт и на карточку проекта (экран 1), и на экран 3, и на панель задач.
  - `main.py`: папка проектов по умолчанию `%USERPROFILE%\Videos\Той Студия` (на других ОС `~/Movies/Той Студия` или `~/Videos/Той Студия`), `app.db` в `%APPDATA%\ToyStudio\app.db` (`~/.toystudio/app.db` вне Windows). Закрытие окна во время анализа спрашивает «Анализ ещё идёт. Свернуть в трей или остановить? Пройденные этапы сохранятся.» с «Свернуть», «Остановить и закрыть».
  - Все новые строки Tasks 8–12 в `ky.ts` с переводом; после `pyside6-lupdate` нет строк без перевода (`type="unfinished"`), кроме помеченных в комментарии к PR для вычитки носителем (фаза 6).

- [ ] **Step 1: Сквозной тест** `test_flow_phase2.py` (`@needs_ffmpeg`, pytest-qt, offscreen, реальный исполнитель): пустой экран 1 → «Новый проект» → добавить 2 клипа по 6 с (один из них в папке с кириллицей) → строки «Готово ✓» → «Начать анализ» → экран 3 → дождаться `finished` (таймаут 120 с) → все этапы `done` или `later` → назад на экран 1 → карточка со статусом «Анализ завершён» и превью. Второй тест: остановить анализ на этапе 1, закрыть `Shell`, создать новый `Shell` → карточка открывает экран 3 с «Продолжить анализ» → продолжить → `finished`.
- [ ] **Step 2:** FAIL → **Step 3:** связать экраны, перевести строки → **Step 4:** весь `pytest`, `ruff check`, `mypy`, `lint-imports` — PASS; ручной запуск `python -m toystudio.app.main` на Mac и проверка на ноутбуке Windows (экраны 1–3 на 1280×720 и 125 %; анализ двух реальных файлов).
- [ ] **Step 5: Commit** `feat(app): phase 2a navigation, translations and end-to-end flow`.

---

## Порядок и зависимости

Tasks 1 → 2 → 3 независимы от UI; 4 → 5 → 6 — движок; 7 собирает их; 8 → 9 → 10 → 11 → 12 — интерфейс. Tasks 3, 5, 6 и 8 не зависят друг от друга и могут идти в любом порядке после своих Consumes. Сквозной тест (Task 12) — единственный, которому нужны все.

## Критерий выхода фазы 2а

- Все тесты зелёные на CI (Windows и Ubuntu).
- На ноутбуке Windows: проект из двух реальных файлов (или синтетики S3, если записей ещё нет) создаётся, анализируется, переживает «Остановить» и закрытие программы, продолжает с места остановки; экраны 1–3 на 1280×720 и 125 % без обрезанных текстов на ru и ky.
- Скорость анализа синтетики S3 на ноутбуке не ниже 90 % от цифр S3 (8,1× / 4,2× / 3,7× / 0,7×) — продукт не медленнее спайка.
