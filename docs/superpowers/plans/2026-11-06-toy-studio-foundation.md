# Той Студия — фаза 1 «Фундамент»: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Репозиторий, в котором собирается и запускается пустая оболочка «Той Студия» с темой из токенов и живым переключением RU/KY. Под ней — протестированное ядро: модели, таймлайн с командами и историей, хранилище проекта в SQLite и менеджер процессов-исполнителей.

**Architecture:** Слои `core` (чистый Python), `storage`, `jobs`, `app` (Qt) по правилу зависимостей из `docs/architecture.md` §3.2. Таймлайн неизменяемый, команды — чистые функции, время — целые микросекунды.

**Tech Stack:** Python 3.12, PySide6 ≥ 6.8, uv, pytest, pytest-qt, ruff, mypy, import-linter, SQLite (stdlib `sqlite3`).

**Spec:** `docs/architecture.md` + дизайн-система (claude.ai/artifact/CL1yyrtm3XzrovRgnNQRxw): `README.md`, `screens.md`, `tokens.json`.

## Global Constraints

- Python `>=3.12,<3.13`; PySide6 `>=6.8`; только Windows 10/11 x64 для релиза, но `core/`, `storage/` и `jobs/` тесты проходят и на macOS/Linux (разработка).
- Время в моделях — `int` микросекунды; `float` секунд допустим только на границе с внешним миром (ffmpeg, JSON плана).
- `core/` не импортирует `PySide6`, `sqlite3`, `subprocess` (проверяется import-linter).
- Минимальный размер окна — 1280×720; верхняя панель — 48 px (`size-topbar`).
- Тексты: исходник на русском в `tr()`, перевод в `toystudio/app/i18n/ky.ts`. Переключатель «Русский · Кыргызча», смена без перезапуска.
- Десятичный разделитель — запятая в обоих языках: `8,4 ГБ`; таймкод `01:12:40`.
- Все записи файлов в `storage/` и `jobs/` — через `atomic_write`.
- Пути с кириллицей и пробелами (`D:\Той Студия\Свадьба Айбек и Нурайым`) — обычный случай.

## Review Focus

1. **Путь проекта с кириллицей и пробелами** — создание, открытие и список проектов работают. Тест в Task 6: `test_store_cyrillic_path`.
2. **Кыргызская строка длиннее русской на 30%+** — кнопки не обрезают текст молча: показывают `…` и подсказку с полным текстом. Тест в Task 8: `test_elided_button_tooltip`.
3. **Обрезка и разрез в точке, не совпадающей с кадром** (25 к/с против 29,97) — не создают отрезков короче 1 кадра и не выходят за границы момента. Тест в Task 4: `test_split_rejects_too_close_to_edge`, `test_trim_clamps_to_bounds`.
4. **Исполнитель убит посреди записи** — на диске нет полузаписанного файла, задание переходит в `failed`, а не висит в `running`. Тесты в Task 7: `test_worker_crash_marks_failed`, Task 6: `test_atomic_write_no_partial`.
5. **Программа закрыта во время задания** — при следующем запуске задание в состоянии `interrupted`, его можно продолжить. Тест в Task 6: `test_running_jobs_become_interrupted_on_open`.

---

## Файлы

```
pyproject.toml, .importlinter, .github/workflows/ci.yml
design/tokens.json                         копия из дизайн-системы
tools/gen_theme.py
toystudio/__init__.py
toystudio/core/{__init__,models,timeline,timeline_ops,history,format}.py
toystudio/storage/{__init__,fs,project_store}.py
toystudio/storage/migrations/0001_init.sql
toystudio/jobs/{__init__,protocol,manager,worker_main}.py
toystudio/app/{__init__,main}.py
toystudio/app/theme/{__init__,theme.qss.tmpl,apply}.py   + generated/
toystudio/app/i18n/{ky.ts,ky.qm}
toystudio/app/widgets/{elided,top_bar}.py
toystudio/app/shell.py
tests/... зеркально
```

---

### Task 1: Каркас репозитория и CI

**Files:**
- Create: `pyproject.toml`, `.importlinter`, `.github/workflows/ci.yml`, `toystudio/__init__.py`, `tests/test_smoke.py`

**Interfaces:**
- Produces: пакет `toystudio` с `__version__ = "0.1.0"`; команды `uv run pytest`, `uv run ruff check`, `uv run mypy toystudio/core`, `uv run lint-imports`.

- [ ] **Step 1: Write the failing test** `tests/test_smoke.py::test_version` — `assert toystudio.__version__ == "0.1.0"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_smoke.py -v` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement.** `pyproject.toml`: зависимости `PySide6>=6.8`; dev — `pytest`, `pytest-qt`, `ruff`, `mypy`, `import-linter`. `[tool.mypy]` strict для `toystudio.core`. `.importlinter`: контракт `layers` — `toystudio.app` > `toystudio.jobs` > (`toystudio.storage` | `toystudio.cloud` | `toystudio.engine`) > `toystudio.core`; плюс forbidden: `toystudio.core` ↛ `PySide6`, `sqlite3`, `subprocess`. CI: `windows-latest` и `ubuntu-latest`, шаги ruff → mypy → lint-imports → pytest (`QT_QPA_PLATFORM=offscreen`).
- [ ] **Step 4: Run** `uv run pytest -v && uv run lint-imports` → PASS.
- [ ] **Step 5: Commit** `chore: project skeleton, CI, layer contracts`.

### Task 2: Генератор темы из tokens.json

**Files:**
- Create: `design/tokens.json` (дословная копия `project/tokens.json` дизайн-системы), `tools/gen_theme.py`, `toystudio/app/theme/theme.qss.tmpl`, `toystudio/app/theme/generated/` (вывод, коммитится)
- Test: `tests/tools/test_gen_theme.py`

**Interfaces:**
- Produces:
  - `load_tokens(path: Path) -> Tokens`, где `Tokens.colors: dict[str, dict[str, str]]` (тема → имя → значение), `Tokens.sizes: dict[str, int]` (px из spacing/radius/size), `Tokens.type_styles: dict[str, TypeStyle]`.
  - `render_qss(template: str, colors: dict[str, str], sizes: dict[str, int]) -> str` — заменяет `{{name}}`; неизвестное имя → `KeyError` с именем.
  - `render_tokens_py(tokens: Tokens) -> str` — модуль с `DARK: dict[str,str]`, `LIGHT: dict[str,str]`, `SIZES: dict[str,int]`, `TYPE: dict[str, tuple[str,int,int,int]]` (family, px, line px, weight).
  - `contrast_ratio(fg: str, bg: str) -> float` (WCAG 2.1, hex `#rrggbb`).
  - CLI: `python tools/gen_theme.py design/tokens.json toystudio/app/theme/generated`.

- [ ] **Step 1: Write failing tests:**

```python
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
@pytest.mark.parametrize("fg,bg,minimum", [
    ("ink", "bg-100", 4.5), ("ink-muted", "bg-200", 4.5), ("ink-faint", "bg-100", 4.5),
    ("ink-on-accent", "garnet-500", 4.5), ("garnet-text", "bg-200", 4.5),
    ("line-200", "bg-200", 3.0), ("ink-on-title", "track-title", 4.5),
])
def test_contrast(theme, fg, bg, minimum):
    c = load_tokens(TOKENS).colors[theme]
    assert contrast_ratio(c[fg], c[bg]) >= minimum

def test_generated_is_fresh(tmp_path):   # сгенерированное в репозитории совпадает с генератором
    main([str(TOKENS), str(tmp_path)])
    for name in ("theme_dark.qss", "theme_light.qss", "tokens.py"):
        assert (tmp_path / name).read_text("utf-8") == (GENERATED / name).read_text("utf-8")
```

- [ ] **Step 2: Run** `uv run pytest tests/tools/test_gen_theme.py -v` → FAIL.
- [ ] **Step 3: Implement** `tools/gen_theme.py` и шаблон. Шаблон покрывает: `QMainWindow`/`QWidget` фон `bg-000`; `QFrame#Panel` `bg-100`; `QFrame#Card` `bg-200`, `radius-lg`, рамка 1px `line-100`; `QPushButton` высота `size-control`, `radius-md`, шрифт `label`; `QPushButton[variant="primary"]` `garnet-500`/`ink-on-accent`, hover `garnet-600`, `min-height: size-control-lg`; `QPushButton[variant="ghost"]`; `QPushButton[chip="true"]` `radius-pill`, `:checked` → `garnet-tint` + рамка `garnet-500`; `QLabel[badge="ready|done|error|offline|analysis"]` по таблице StatusBadge; `QLineEdit, QPlainTextEdit` `bg-200`, рамка `line-200`, фокус 2px `focus`; `QWidget#TopBar` высота `size-topbar`. Если тест контраста не проходит из-за значения в токенах, токен не меняем: помечаем тест `xfail` со ссылкой на токен и сообщаем дизайнеру.
- [ ] **Step 4: Run** генератор, затем тесты → PASS.
- [ ] **Step 5: Commit** `feat(theme): generate QSS and tokens.py from design tokens`.

### Task 3: Модели ядра и форматирование

**Files:**
- Create: `toystudio/core/models.py`, `toystudio/core/format.py`
- Test: `tests/core/test_models.py`, `tests/core/test_format.py`

**Interfaces:**
- Produces:
  - `US_PER_S = 1_000_000`
  - `class MomentType(StrEnum)`: `TOAST="toast"`, `DANCE="dance"`, `ENTRANCE="entrance"`, `GIFTS="gifts"`, `APPLAUSE="applause"`, `MUSIC="music"`
  - `class Mark(StrEnum)`: `NONE`, `MUST`, `EXCLUDE`
  - `class ToiKind(StrEnum)`: `WEDDING`, `TOI`, `JUBILEE`, `BESH_BARMAK`, `SUNNOT`, `KYZ_UZATUU`, `OTHER`
  - `class Track(StrEnum)`: `VIDEO`, `MUSIC`, `TITLE`; `class AudioMode(StrEnum)`: `ORIGINAL`, `MUSIC`, `DUCK`
  - `@dataclass(frozen=True) class Reason: code: str; ru: str; ky: str`
  - `@dataclass(frozen=True) class Moment: id, media_id, type: MomentType, start: int, end: int, score: float, mark: Mark = Mark.NONE` с `duration` (property)
  - `format_timecode(us: int) -> str` (`"01:12:40"` при ≥ 1 ч, иначе `"04:58"`); `format_size(bytes_: int) -> str` (`"8,4 ГБ"`, `"640 МБ"`); `format_duration_words(us: int, lang: str) -> str` (RU `"3 ч 40 мин"`, KY `"3 саат 40 мүн"` — KY-форму подтвердить у носителя языка).

- [ ] **Step 1: Failing tests:** `format_timecode(4_352_000_000) == "01:12:32"`; `format_timecode(298_000_000) == "04:58"`; `format_size(9_019_431_321) == "8,4 ГБ"`; `format_size(671_088_640) == "640 МБ"`; `Moment(..., start=10*US_PER_S, end=40*US_PER_S).duration == 30*US_PER_S`; `Moment` с `end <= start` → `ValueError`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Размер — двоичные единицы (1 ГБ = 1024³), одна цифра после запятой для ГБ, целое для МБ. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(core): domain models and RU/KY formatting`.

### Task 4: Таймлайн и команды

**Files:**
- Create: `toystudio/core/timeline.py`, `toystudio/core/timeline_ops.py`
- Test: `tests/core/test_timeline_ops.py`

**Interfaces:**
- Consumes: `Track`, `AudioMode`, `Reason`, `US_PER_S` (Task 3).
- Produces:
  - `Segment` — поля как в `docs/architecture.md` §8.1; `duration` property; `__post_init__` проверяет `bound_in <= src_in < src_out <= bound_out`.
  - `Timeline(video: tuple[Segment,...] = (), music: tuple[Segment,...] = (), titles: tuple[Segment,...] = ())`; методы `duration() -> int` (сумма видео), `starts(track) -> list[int]`, `index_of(seg_id) -> tuple[Track,int]`, `segment(seg_id) -> Segment`, `map_position(old: Timeline, pos: int) -> int` (позиция внутри того же отрезка в новой раскладке; если отрезок удалён — начало следующего).
  - `MIN_SEGMENT_US = 40_000` (1 кадр при 25 к/с).
  - Команды (все возвращают новый `Timeline`, ставят `manual=True` на изменённые отрезки): `move(t, seg_id, to_index)`, `trim(t, seg_id, edge: Literal["in","out"], new_src: int)` (зажимает в `[bound_in, bound_out]` и не делает отрезок короче `MIN_SEGMENT_US`), `delete(t, seg_id)` (удаляет и привязанные титры), `split(t, seg_id, at_timeline: int, new_id: str)` (`ValueError`, если до любого края < `MIN_SEGMENT_US`), `set_locked(t, seg_id, locked)`, `set_audio_mode(t, seg_id, mode)`.
  - `duration_hint(t: Timeline, target_us: int) -> tuple[int,int] | None` — возвращает `(факт, цель)`, если отклонение > 10%, иначе `None` (screens §5 «Клип стал 4:31 из 5:00»).

- [ ] **Step 1: Failing tests** (фикстура `tl3()`: три видео-отрезка a/b/c по 10 с, `bound` ±5 с, титр `t1` с `anchor_id="b"`):

```python
def test_delete_closes_gap():
    t = delete(tl3(), "b")
    assert [s.id for s in t.video] == ["a", "c"] and t.duration() == 20 * US_PER_S
    assert t.titles == ()                       # привязанный титр ушёл вместе с отрезком

def test_move_sets_manual_only_on_moved():
    t = move(tl3(), "c", 0)
    assert [s.id for s in t.video] == ["c", "a", "b"]
    assert [s.manual for s in t.video] == [True, False, False]

def test_trim_clamps_to_bounds():
    s = tl3().segment("a")
    t = trim(tl3(), "a", "in", s.bound_in - 99 * US_PER_S)
    assert t.segment("a").src_in == s.bound_in

def test_trim_keeps_min_length():
    s = tl3().segment("a")
    t = trim(tl3(), "a", "out", s.src_in)
    assert t.segment("a").duration == MIN_SEGMENT_US

def test_split_at_position():
    t = split(tl3(), "b", 15 * US_PER_S, "b2")     # середина b
    assert [s.id for s in t.video] == ["a", "b", "b2", "c"]
    assert t.segment("b").duration == t.segment("b2").duration == 5 * US_PER_S

def test_split_rejects_too_close_to_edge():
    with pytest.raises(ValueError):
        split(tl3(), "b", 10 * US_PER_S + 20_000, "b2")

def test_duration_hint_threshold():
    assert duration_hint(tl3(), 30 * US_PER_S) is None
    assert duration_hint(delete(tl3(), "b"), 30 * US_PER_S) == (20 * US_PER_S, 30 * US_PER_S)
    assert duration_hint(tl3(), 33 * US_PER_S) is None          # 9,1% — ещё не подсказка

def test_map_position_after_move():
    old = tl3(); new = move(old, "c", 0)
    assert new.map_position(old, 25 * US_PER_S) == 5 * US_PER_S  # середина c теперь в начале
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_timeline_ops.py -v` → FAIL.
- [ ] **Step 3: Implement.** Кортежи и `dataclasses.replace`; без копирования неизменённых отрезков.
- [ ] **Step 4: Run** → PASS; `uv run mypy toystudio/core` → без ошибок.
- [ ] **Step 5: Commit** `feat(core): immutable timeline with editing commands`.

### Task 5: История отмены

**Files:**
- Create: `toystudio/core/history.py`
- Test: `tests/core/test_history.py`

**Interfaces:**
- Consumes: `Timeline` (Task 4).
- Produces: `class History(limit: int = 200)`: `current -> Timeline`, `push(t: Timeline) -> None` (сбрасывает redo), `undo() -> Timeline | None`, `redo() -> Timeline | None`, `can_undo`, `can_redo`.

- [ ] **Step 1: Failing tests:** push a→b→c, `undo()` даёт b, `undo()` даёт a, `undo()` даёт `None`; `redo()` даёт b; push d после undo → `can_redo is False`; при `limit=3` после 5 push `undo` возможен ровно 2 раза.
- [ ] **Step 2–4:** Run → FAIL, реализовать на `collections.deque(maxlen=limit)`, Run → PASS.
- [ ] **Step 5: Commit** `feat(core): bounded undo/redo history`.

### Task 6: Хранилище проекта

**Files:**
- Create: `toystudio/storage/fs.py`, `toystudio/storage/serde.py`, `toystudio/storage/project_store.py`, `toystudio/storage/app_store.py`, `toystudio/storage/migrations/0001_init.sql`
- Test: `tests/storage/test_fs.py`, `tests/storage/test_project_store.py`

**Interfaces:**
- Consumes: `Timeline`, `Segment`, `ToiKind` (Tasks 3–4).
- Produces:
  - `atomic_write(path: Path, data: bytes) -> None` — запись в `path.with_suffix(path.suffix + ".tmp")`, `fsync`, `os.replace`.
  - `timeline_to_json(t: Timeline) -> str` / `timeline_from_json(s: str) -> Timeline` (в `toystudio/storage/serde.py`).
  - `ProjectStore.create(root: Path, name: str, kind: ToiKind, event_date: date) -> ProjectStore` — создаёт папку, `project.db` (WAL, `PRAGMA user_version=1`) и `cache/{proxy,audio,frames,thumbs,wave,export}`.
  - `ProjectStore.open(root: Path) -> ProjectStore` — применяет недостающие миграции и делает копию `project.db.bak`; задания `running` переводит в `interrupted`.
  - `ProjectStore.create_cut(title: str, aspect: str, target_us: int) -> str`; `save_timeline(cut_id: str, t: Timeline) -> int` (seq), хранит последние 50; `load_timeline(cut_id) -> Timeline | None`.
  - `ProjectStore.set_job_state(job_id: str, state: str)` и `job_state(job_id) -> str` (таблица `job` в `0001_init.sql` вместе со всеми таблицами из `docs/architecture.md` §4.2).
  - `AppStore(path: Path)`: `register_project(root: Path, name, status, updated_at)`, `list_projects() -> list[ProjectRow]` — сначала `status == "analysis"`, затем по `updated_at` убыв. (screens §1).

- [ ] **Step 1: Failing tests:**

```python
def test_atomic_write_no_partial(tmp_path, monkeypatch):
    p = tmp_path / "x.json"; atomic_write(p, b"old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError): atomic_write(p, b"new")
    assert p.read_bytes() == b"old"

def test_store_cyrillic_path(tmp_path):
    root = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    s = ProjectStore.create(root, "Свадьба Айбек и Нурайым", ToiKind.WEDDING, date(2026, 10, 12))
    assert ProjectStore.open(root).name == "Свадьба Айбек и Нурайым"
    assert (root / "cache" / "proxy").is_dir()

def test_timeline_roundtrip(tmp_path):
    s = new_store(tmp_path); cut = s.create_cut("Клип 5 мин", "16:9", 300 * US_PER_S)
    s.save_timeline(cut, tl3()); assert s.load_timeline(cut) == tl3()

def test_snapshot_retention(tmp_path):
    s = new_store(tmp_path); cut = s.create_cut("x", "16:9", 1)
    for _ in range(60): s.save_timeline(cut, tl3())
    assert s.snapshot_count(cut) == 50

def test_running_jobs_become_interrupted_on_open(tmp_path):
    s = new_store(tmp_path); s.set_job_state("j1", "running"); s.close()
    assert ProjectStore.open(s.root).job_state("j1") == "interrupted"

def test_list_projects_analysis_first(tmp_path):
    a = AppStore(tmp_path / "app.db")
    a.register_project(Path("A"), "A", "ready", datetime(2026, 11, 5))
    a.register_project(Path("B"), "B", "analysis", datetime(2026, 11, 1))
    a.register_project(Path("C"), "C", "done", datetime(2026, 11, 6))
    assert [r.name for r in a.list_projects()] == ["B", "C", "A"]
```

(`tl3()` вынести в `tests/conftest.py` из Task 4.)
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; соединение `sqlite3.connect(str(path))` с `PRAGMA journal_mode=WAL; foreign_keys=ON`. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(storage): project folder, SQLite schema, timeline snapshots`.

### Task 7: Протокол и менеджер исполнителей

**Files:**
- Create: `toystudio/jobs/protocol.py`, `toystudio/jobs/manager.py`, `toystudio/jobs/worker_main.py`
- Test: `tests/jobs/test_protocol.py`, `tests/jobs/test_manager.py`, `tests/jobs/fake_worker.py`

**Interfaces:**
- Produces:
  - `protocol.py`: `@dataclass Progress(stage: str, done: float, total: float, media_id: str | None)`, `Event(kind: str, payload: dict)`, `Done()`, `Failed(code: str, detail: str)`; `encode(msg) -> bytes` (одна JSON-строка + `\n`, ключ `"t"` = тип); `decode(line: bytes) -> Message`; неизвестный `"t"` → `ValueError`.
  - `worker_main.py`: `main(argv: list[str]) -> int` — `--job <kind> --project <path> --job-id <id>`; реестр `JOB_KINDS: dict[str, Callable[[JobContext], None]]` (пока только `"noop"` для тестов); `JobContext.report(msg)` пишет `encode(msg)` в stdout с `flush`.
  - `manager.py`: `class JobManager(QObject)` с сигналами `progress(str, object)`, `event(str, object)`, `finished(str)`, `failed(str, str, str)` (job_id, code, detail); `start(job_id: str, argv: list[str]) -> None`; `cancel(job_id) -> None` (`kill()` → `failed(job_id, "cancelled", "")`); `running() -> list[str]`; аварийный выход процесса без `Done` → `failed(job_id, "crashed", <хвост stderr ≤ 4 КБ>)`; одновременно запускается не больше `max_parallel=1`, остальные ждут в очереди; дросселирование `progress` — не чаще 4 раз в секунду на задание.

- [ ] **Step 1: Failing tests:** roundtrip `decode(encode(Progress("asr", 3, 10, "m1"))) == Progress(...)`; `decode(b'{"t":"zzz"}')` → `ValueError`. Для менеджера `fake_worker.py` по аргументу ведёт себя как `ok` (10 progress + Done), `crash` (2 progress, затем `os._exit(3)`), `hang` (спит):

```python
def test_worker_ok(qtbot):
    m = JobManager()
    with qtbot.waitSignal(m.finished, timeout=10_000) as sig:
        m.start("j1", [sys.executable, FAKE, "ok"])
    assert sig.args == ["j1"]

def test_worker_crash_marks_failed(qtbot):
    m = JobManager()
    with qtbot.waitSignal(m.failed, timeout=10_000) as sig:
        m.start("j2", [sys.executable, FAKE, "crash"])
    assert sig.args[:2] == ["j2", "crashed"]

def test_cancel(qtbot):
    m = JobManager(); m.start("j3", [sys.executable, FAKE, "hang"])
    with qtbot.waitSignal(m.failed, timeout=10_000) as sig: m.cancel("j3")
    assert sig.args[:2] == ["j3", "cancelled"]

def test_queue_one_at_a_time(qtbot):
    m = JobManager(max_parallel=1)
    m.start("a", [sys.executable, FAKE, "hang"]); m.start("b", [sys.executable, FAKE, "ok"])
    assert m.running() == ["a"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**: `QProcess.readyReadStandardOutput` + буфер по строкам; приоритет процесса `BELOW_NORMAL` на Windows через `psutil` после `started` (на других ОС пропускаем). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(jobs): worker protocol and QProcess job manager`.

### Task 8: Оболочка приложения, тема, языки

**Files:**
- Create: `toystudio/app/main.py`, `toystudio/app/shell.py`, `toystudio/app/theme/apply.py`, `toystudio/app/widgets/elided.py`, `toystudio/app/widgets/top_bar.py`, `toystudio/app/i18n/ky.ts`; шрифты `toystudio/app/fonts/{Inter-*.ttf, JetBrainsMono-*.ttf}` (скачать с официальных релизов, приложить OFL)
- Test: `tests/app/test_shell.py`, `tests/app/test_elided.py`

**Interfaces:**
- Consumes: `generated/theme_*.qss`, `generated/tokens.py` (Task 2).
- Produces:
  - `apply_theme(app: QApplication, theme: Literal["dark","light"]) -> None`; `set_variant(w: QWidget, name: str, value: str) -> None` (setProperty + unpolish/polish).
  - `load_fonts() -> None` (через `QFontDatabase.addApplicationFont`; ошибка загрузки → запасной Segoe UI, но в лог).
  - `class ElidedButton(QPushButton)` и `class ElidedLabel(QLabel)`: полный текст в `full_text()`, отображается `fontMetrics().elidedText(..., Qt.ElideRight, width)`; если текст обрезан — `toolTip() == full_text()`, иначе подсказка пустая.
  - `class TopBar(QWidget)` (`objectName="TopBar"`, высота 48): заголовок-логотип, степпер `Файлы → Анализ → Задание → Черновик → Экспорт` (`set_step(i: int, reachable: int)`), переключатель языка `ru`/`ky`, переключатель темы; сигналы `language_changed(str)`, `theme_changed(str)`.
  - `class Shell(QMainWindow)`: `minimumSize == QSize(1280, 720)`, центральный `QStackedWidget` с заглушкой «Проекты»; `set_language(code: Literal["ru","ky"])` ставит или снимает `QTranslator` и вызывает `retranslate()` у себя и детей через `QEvent.LanguageChange`.
  - `main() -> int` — точка входа `ToyStudio.exe`: один экземпляр (`QLocalServer` с именем `ToyStudio-<user>`), тёмная тема по умолчанию.

- [ ] **Step 1: Failing tests:**

```python
def test_language_switch_live(qtbot):
    w = Shell(); qtbot.addWidget(w)
    assert w.page_title() == "Проекты"
    w.set_language("ky"); assert w.page_title() == "Долбоорлор"
    w.set_language("ru"); assert w.page_title() == "Проекты"

def test_stepper_labels_ky(qtbot):
    w = Shell(); qtbot.addWidget(w); w.set_language("ky")
    assert w.top_bar.step_labels()[1] == "Талдоо"

def test_theme_switch(qtbot):
    w = Shell(); qtbot.addWidget(w)
    apply_theme(QApplication.instance(), "light")
    assert "#e9eaed" in QApplication.instance().styleSheet()

def test_minimum_size(qtbot):
    w = Shell(); qtbot.addWidget(w); assert w.minimumSize() == QSize(1280, 720)

def test_elided_button_tooltip(qtbot):
    b = ElidedButton("Жаңы тапшырма менен кайра жасоо"); qtbot.addWidget(b)
    b.resize(120, 32); b.show()
    assert b.toolTip() == "Жаңы тапшырма менен кайра жасоо"
    b.resize(600, 32); assert b.toolTip() == ""
```

- [ ] **Step 2: Run** `QT_QPA_PLATFORM=offscreen uv run pytest tests/app -v` → FAIL.
- [ ] **Step 3: Implement**; в `ky.ts` — строки из таблицы README дизайн-системы (Проекты → Долбоорлор, Новый проект → Жаңы долбоор, Анализ → Талдоо, Черновик → Кара көчүрмө, Экспорт → Экспорт, Файлы → Файлдар, Задание → Тапшырма) с пометкой `unfinished`, пока их не вычитал носитель языка. `.qm` собирается `pyside6-lrelease` в CI и в тестах (фикстура в `conftest.py`).
- [ ] **Step 4: Run** → PASS. Ручная проверка: `uv run python -m toystudio.app.main` открывает тёмное окно с верхней панелью, RU/KY и тема переключаются без перезапуска.
- [ ] **Step 5: Commit** `feat(app): shell window, theme, live RU/KY switch`.

---

## Не входит в фазу 1 (следующие планы)

Анализ и `engine/` (фаза 2), плеер mpv и таймлайн на `QGraphicsView` (фаза 4), фиттер и plan-service (фаза 3), экспорт (фаза 5), установщик Inno Setup и подпись (фаза 6; в фазе 1 достаточно, что CI собирает `pyinstaller` onedir без ошибок — добавить шаг в `ci.yml` в Task 1, если PySide6 уже установлен).
