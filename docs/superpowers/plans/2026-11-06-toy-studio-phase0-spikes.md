# Той Студия — фаза 0 «Спайки»: план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Цифрами ответить на пять вопросов, от которых зависит фаза 2 и дальше: какой моделью распознавать речь, как встраивать mpv, сколько стоит анализ, годится ли план от модели, держит ли синхрон чанковый экспорт. Результат каждого спайка — записка `docs/spikes/S<N>-*.md` с решением. В конце — сводка решений и правки в `docs/architecture.md`.

**Architecture:** Спайк-код живёт в `spikes/` вне пакета `toystudio`. Он не продакшен, но воспроизводим: каждый замер — один скрипт, вход — файл или папка, выход — JSON с результатами в `spikes/results/`. Общие утилиты лежат в `spikes/common/` и покрыты тестами. Всё остальное — исследовательский код, тесты для него не обязательны.

**Tech Stack:** Python 3.12+, ffmpeg/ffprobe, libmpv + python-mpv, PySide6, faster-whisper / CTranslate2, jiwer, numpy, OpenCV (headless), anthropic SDK.

**Spec:** `docs/architecture.md` — §2 риски R1–R4, §5 конвейер анализа, §7 план, §8.2 плеер, §9 экспорт, §13 фаза 0.

## Global Constraints

- **Реальные записи тоев никогда не попадают в git и в сеть.** Они лежат в `spikes/data/` (в `.gitignore`), в репозиторий идут только синтетические файлы из генератора и JSON с цифрами. В облачную модель уходит только текст (как в §7.1).
- Сообщения коммитов — на английском, без строк об ИИ (без `Co-Authored-By`, без упоминаний Claude).
- Каждый спайк ограничен по времени (таблица ниже). Если время вышло, а ответа нет, пишем записку с тем, что успели узнать, и запасным решением, а не продлеваем спайк молча.
- Цифры производительности считаются только с эталонного Windows-ПК (§1.1 архитектуры: 6 ядер класса i5-10400, 16 ГБ, с GTX 1650/1660 и без GPU). Цифры с Mac M1 — только для отладки скриптов, в записке они помечены «Mac, не для решения».
- Время — целые микросекунды в результатах, как в продукте.
- Все скрипты принимают пути аргументами и работают с кириллицей и пробелами в путях.

## Сводка спайков

| Спайк | Вопрос | Где | Срок | Блокер |
| --- | --- | --- | --- | --- |
| S2 | Плеер: mpv в PySide6 — бесшовно, быстро, легально? | Mac (render API), затем Windows (`wid`) | 3 дня | нет |
| S5 | Экспорт: чанки + отдельный звук держат синхрон ≤ 1 кадра? | Mac, затем Windows (NVENC/QSV/AMF) | 2 дня | нет |
| S3 | Анализ: один проход декодирования ≥ 12× реального времени? | Mac для отладки, цифры на Windows | 2 дня | эталонный ПК |
| S1 | Речь: какая модель даёт приемлемую ошибку на кыргызском и русском в зале? | Mac CPU + Windows CPU/GPU | 4 дня | **записи 3 тоев + эталонные расшифровки** |
| S4 | План: модель собирает осмысленный черновик по тексту? | Mac | 3 дня | записи 2 тоев (через S1), ключ API, монтажёр |

Порядок: Task 1 → S2, S5, S3 (можно начинать сейчас) → S1 → S4 → сводка. S2 и S5 не зависят друг от друга.

## Что нужно от владельца продукта до S1 и S4

1. 3–10 записей тоев (минимум 3) с письменным согласием семей и операторов, лучше с 2 камер. Сырые файлы с камер или телефонов, без перекодирования.
2. Для каждого из 3 тоев эталонная расшифровка **10 минут речи** (2–3 тоста и ведущий), сделанная человеком. Формат: CSV `file,start,end,lang,text`, время — `ЧЧ:ММ:СС.мс`.
3. Разметка монтажёра для тех же тоев: где тосты, танцы, выход молодых (CSV `file,start,end,type`).
4. Ключ Claude API в переменной окружения `ANTHROPIC_API_KEY` на машине, где запускается S4. Я не ввожу ключи сам.
5. Доступ к Windows-ПК эталонного класса (лично или через удалённый доступ), где можно ставить ffmpeg, Python и драйверы.
6. Один монтажёр на 1–2 часа для оценки планов S4.

## Review Focus

1. **Кириллица и пробелы в путях** — ffmpeg, mpv EDL и Python-скрипты получают `D:\Той Студия\Свадьба Айбек и Нурайым\IMG_0412.MP4`. Тест в Task 1: `test_generate_cyrillic_path`.
2. **Смешанная частота кадров** (25, 29,97, 50) в одном экспорте — синхрон держится по всему клипу, а не только в начале. Проверка в Task 3.
3. **Телефонное видео: HEVC 10 бит HLG и поворот 90°** — копия для анализа выходит SDR, правильной ориентации. Проверка в Task 4.
4. **Шумный зал с музыкой** — ошибка распознавания считается на реальном звуке, а не на чистых фразах. Проверка в Task 5.
5. **Реальные записи не утекают** — в git нет ни одного файла из `spikes/data/`, запрос к модели содержит только текст. Проверка в Task 1 (`test_gitignore_covers_data`) и Task 6.

---

## Файлы

```
spikes/README.md                    как запускать, где лежат данные, правила приватности
spikes/common/{__init__,media,results,detect}.py
spikes/s2_player/{render_widget,wid_widget,bench_player}.py
spikes/s5_export/{build_chunks,measure_sync}.py
spikes/s3_decode/{single_pass,bench_decode}.py
spikes/s1_asr/{convert_models,bench_asr,refs}.py
spikes/s4_plan/{build_request,prompt_v0.md,schema_v0.json,run_plan,score_plan}.py
spikes/results/                     JSON с замерами (коммитятся)
spikes/data/                        реальные записи (gitignore)
tests/spikes/test_common.py
docs/spikes/S1-asr.md … S5-export.md, docs/spikes/phase0-decisions.md
```

`pyproject.toml`: группа зависимостей `spikes` (`python-mpv`, `faster-whisper`, `ctranslate2`, `transformers`, `jiwer`, `numpy`, `opencv-python-headless`, `anthropic`, `jsonschema`). Основной пакет от неё не зависит.

---

### Task 1: Общий каркас спайков

**Files:**
- Create: `spikes/README.md`, `spikes/common/media.py`, `spikes/common/results.py`, `spikes/common/detect.py`, `tests/spikes/test_common.py`
- Modify: `.gitignore` (`spikes/data/`), `pyproject.toml` (группа `spikes`; `pythonpath` для тестов уже `.`)

**Interfaces:**
- Produces:
  - `generate_clip(path: Path, *, duration_s: float, fps: str, size: str = "1920x1080", flash_every_s: float = 2.0, codec: str = "libx264") -> Path` — синтетический клип ffmpeg (`testsrc2` + белая вспышка 1 кадр и звуковой «бип» 1 кГц 20 мс **в одни и те же моменты** каждые `flash_every_s`, начиная с 1,0 с). `fps` — строка ffmpeg (`"25"`, `"30000/1001"`, `"50"`).
  - `generate_set(dir: Path) -> list[Path]` — набор для S2/S5: 3 клипа по 60 с (25, 30000/1001, 50 к/с) и музыкальный трек 120 с (синус с огибающей).
  - `probe(path: Path) -> dict` — обёртка ffprobe (`streams`, `format`) с JSON-выводом.
  - `detect_flashes(video: Path) -> list[int]` — мкс кадров, где средняя яркость > 200 (ffmpeg `signalstats` → `YAVG`).
  - `detect_beeps(video_or_audio: Path) -> list[int]` — мкс начала бипов (огибающая 1 кГц полосы, порог, дедупликация 100 мс).
  - `write_result(spike: str, name: str, data: dict) -> Path` — пишет `spikes/results/<spike>/<name>-<UTC timestamp>.json`, всегда добавляя `machine` (ОС, CPU, RAM, GPU — что удаётся узнать), `ffmpeg_version`, `git_rev`.

- [ ] **Step 1: Write failing tests**

```python
def test_generate_cyrillic_path(tmp_path):
    p = generate_clip(tmp_path / "Той Студия" / "клип 25.mp4", duration_s=5, fps="25")
    assert probe(p)["streams"][0]["r_frame_rate"] == "25/1"

def test_flashes_and_beeps_align(tmp_path):
    p = generate_clip(tmp_path / "a.mp4", duration_s=7, fps="30000/1001")
    f, b = detect_flashes(p), detect_beeps(p)
    assert len(f) == len(b) == 3                       # 1,0 / 3,0 / 5,0 с
    assert all(abs(x - y) <= 34_000 for x, y in zip(f, b))   # ≤ 1 кадр при 29,97

def test_gitignore_covers_data():
    out = subprocess.run(["git", "check-ignore", "spikes/data/x.mp4"], capture_output=True)
    assert out.returncode == 0

def test_write_result_has_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path)
    data = json.loads(write_result("s0", "t", {"x": 1}).read_text("utf-8"))
    assert data["x"] == 1 and {"machine", "ffmpeg_version", "git_rev"} <= data.keys()
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/spikes -v` → FAIL.
- [ ] **Step 3: Implement.** Всё через `subprocess.run([...])` со списком аргументов (без `shell=True`), пути как `str(path)`. `README.md` — правила приватности из Global Constraints, как положить данные в `spikes/data/<той>/`, как запускать каждый спайк.
- [ ] **Step 4: Run** → PASS; `ruff check .`, `lint-imports` чистые.
- [ ] **Step 5: Commit** `chore(spikes): shared media generator, detectors, result writer`.

### Task 2: S2 — плеер mpv в PySide6

**Вопросы и пороги (из §8.2 и §1.2):**

| Метрика | Как мерить | Порог |
| --- | --- | --- |
| Бесшовность EDL | `edl://` из 200 отрезков по 0,5–3 с из 3 proxy (одинаковые параметры кодека); считать `frame-drop-count` и `vo-delayed-frame-count`; на каждом стыке снять `screenshot-raw` первого кадра отрезка и сравнить со «правильным» кадром, извлечённым ffmpeg из proxy по `src_in` (средняя абсолютная разница пикселей < 2 из 255) | 0 пропущенных, чёрных или чужих кадров на стыках |
| Перемотка | `seek <t> absolute+exact` → событие `playback-restart`; 100 случайных позиций | p95 ≤ 150 мс |
| Пересборка EDL после правки | `loadfile edl://…` + `seek` на сохранённую позицию | ≤ 300 мс до первого кадра |
| Покадровый шаг | `frame-step` / `frame-back-step` × 50 | без ошибок, ≤ 100 мс на шаг |
| Музыкальная подложка | `--lavfi-complex='[aid1][aid2]amix…[ao]'` с внешним WAV; на стыке EDL звук не щёлкает и не сдвигается | бип клипа совпадает со вспышкой ±1 кадр |
| Титры | `sub-add` ASS с Inter, кириллица «Үйлөнүү тою — Өмүрбек» | видно, без квадратиков |
| Лицензия | `mpv --version` / конфигурация libmpv: собрана ли с `-Dgpl=false`; найти Windows-сборку libmpv под LGPL или рецепт сборки | ответ «да, вот DLL/рецепт» или «нет → запасной план» |

**Важно про встраивание:** на macOS встраивание через `wid` не работает надёжно, поэтому на Mac прототип делаем через **render API** libmpv (`MpvRenderContext` + `QOpenGLWidget`). На Windows проверяем оба варианта: `wid` (проще) и render API (единый код для всех ОС). Решение в записке — какой вариант идёт в продукт.

**Files:** `spikes/s2_player/render_widget.py`, `spikes/s2_player/wid_widget.py`, `spikes/s2_player/bench_player.py`, `docs/spikes/S2-player.md`

- [ ] **Step 1:** Установить зависимости. Mac: `brew install mpv` (даёт libmpv), `pip install python-mpv`. Windows: libmpv-2.dll рядом со скриптом. **Установку системных пакетов согласовать с владельцем машины.**
- [ ] **Step 2:** Сгенерировать proxy из `generate_set` в формате копии для анализа: 960×540, H.264, `-g` = 1 с, CFR, AAC 48 кГц. Параметры кодека у всех трёх одинаковые.
- [ ] **Step 3:** `render_widget.py` — минимальное окно PySide6 с `QOpenGLWidget` и render API, кнопки «пуск/пауза», «перемотка», «шаг», «пересобрать EDL».
- [ ] **Step 4:** `bench_player.py --mode render|wid --segments 200 --out …` прогоняет все метрики таблицы без участия человека и пишет `write_result("s2", …)`.
- [ ] **Step 5:** Прогон на Mac (render) → JSON. Прогон на Windows (render и wid) → JSON.
- [ ] **Step 6:** Записка `docs/spikes/S2-player.md`: цифры, скриншот титров, решение (render или wid; mpv или запасной QtMultimedia), найденная LGPL-сборка или рецепт, риски.
- [ ] **Step 7: Commit** `spike(s2): mpv EDL playback benchmark and decision memo`.

**Запасной план, если mpv не проходит:** QtMultimedia (FFmpeg-бэкенд Qt 6.5+) с двумя `QMediaPlayer` и переключением на стыке; прогнать те же метрики на 50 отрезках, чтобы понять, хуже ли он и насколько.

### Task 3: S5 — чанковый экспорт и синхрон

**Вопросы и пороги (§9):**

| Метрика | Как мерить | Порог |
| --- | --- | --- |
| Синхрон по всему клипу | экспорт 5 мин из 3 синтетических клипов 25 / 29,97 / 50 к/с, ~120 отрезков; `detect_flashes` и `detect_beeps` на результате; разница каждой пары | max \|Δ\| ≤ 1 кадр вывода (40 мс при 25 к/с) от начала до конца |
| Нет дрейфа | линейная регрессия Δ по времени | наклон < 1 мс/мин |
| Стыки чанков | число кадров результата = сумма длительностей × fps (±1 на чанк недопустимо, ±1 на весь клип допустимо) | совпадает |
| Повторное использование чанков | изменить 1 отрезок в середине → сколько чанков рендерится заново | ровно затронутые (1–2) |
| Скорость | время экспорта 5 мин 1080p: libx264 `veryfast`, `h264_videotoolbox` (Mac); `h264_nvenc`, `h264_qsv`, `h264_amf`, libx264 (Windows) | ≤ 3 мин с GPU, ≤ 8 мин CPU на эталонном ПК |
| Сравнение | тот же экспорт одним большим `filter_complex` | для записки: быстрее/медленнее, синхрон |

**Files:** `spikes/s5_export/build_chunks.py`, `spikes/s5_export/measure_sync.py`, `docs/spikes/S5-export.md`

- [ ] **Step 1:** `build_chunks.py edl.json --preset youtube1080 --encoder <name> --out <dir>` реализует схему §9: чанки по ~30 с с именем = хэш входа, одинаковые параметры кодировщика и закрытый GOP, отдельный WAV одним проходом (`atrim` + 10 мс `afade` на стыках + музыка), `concat` demuxer `-c copy` + mux AAC, `+faststart`.
- [ ] **Step 2:** Генератор тестового EDL: 120 отрезков случайной длины 1–5 с из трёх клипов (seed фиксирован, EDL сохраняется в `spikes/results/s5/`).
- [ ] **Step 3:** `measure_sync.py out.mp4` → метрики таблицы → `write_result("s5", …)`.
- [ ] **Step 4:** Прогон на Mac (libx264, videotoolbox), затем на Windows (все доступные кодировщики, обнаружение по тестовому кадру, как в §9 п. 5).
- [ ] **Step 5:** Записка `docs/spikes/S5-export.md`: таблица синхрона и скорости по кодировщикам, решение по размеру чанка (20/30/60 с — замерить все три), подтверждение схемы или правки к §9.
- [ ] **Step 6: Commit** `spike(s5): chunked export sync benchmark and memo`.

### Task 4: S3 — скорость одного прохода декодирования

**Вопросы и пороги (§5.1, §5.3):**

| Метрика | Порог на эталонном ПК |
| --- | --- |
| Скорость прохода (proxy 540p + WAV 16 кГц + кадры 160×90 gray 4 к/с в pipe с расчётом метрик кадра в Python) для 1080p H.264 | ≥ 12× реального времени без GPU (цель 15×) |
| То же с `-hwaccel d3d11va` | замерить прирост |
| 4K HEVC 10 бит HLG (iPhone) | замерить; proxy выходит SDR (`zscale` + `tonemap`) и правильной ориентации при повороте 90° |
| Python-часть (метрики кадра из pipe) не тормозит ffmpeg | загрузка ядра Python-процесса < 50%, pipe не переполняется |
| Пиковая память | < 1,5 ГБ |

**Files:** `spikes/s3_decode/single_pass.py`, `spikes/s3_decode/bench_decode.py`, `docs/spikes/S3-decode.md`

- [ ] **Step 1:** `single_pass.py src --out-dir …` — один вызов ffmpeg с тремя выходами (`-map` на proxy mp4, wav и `rawvideo` в `pipe:1`) и `-progress`. Python читает кадры из pipe и считает резкость (дисперсия лапласиана, OpenCV), яркость, разность кадров и пишет `frames.npz`.
- [ ] **Step 2:** Тестовые исходники: синтетические 1080p25 H.264 и 1080p50 (Task 1), плюс **по одному реальному файлу каждого типа** из `spikes/data/` (камера 1080p, телефон 4K HEVC HLG с поворотом), когда они появятся.
- [ ] **Step 3:** `bench_decode.py` — все варианты (CPU, `d3d11va` / `videotoolbox`, с метриками и без) → `write_result("s3", …)`.
- [ ] **Step 4:** Прогон на Mac (отладка), затем на эталонном Windows-ПК с GPU и без.
- [ ] **Step 5:** Записка `docs/spikes/S3-decode.md`: скорость по типам файлов, ожидаемое время анализа 3 ч 40 мин сырья (этапы 1 и 1b), решение по разрешению proxy и частоте кадров для метрик, профиль ETA по умолчанию.
- [ ] **Step 6: Commit** `spike(s3): single-pass decode throughput and memo`.

### Task 5: S1 — распознавание речи ru/ky

**Блокер:** записи 3 тоев и эталонные расшифровки 10 минут на каждый (раздел «Что нужно от владельца продукта»).

**Кандидаты** (у каждого перед прогоном проверить лицензию на странице модели и записать её в записку):

| Модель | Откуда | Зачем |
| --- | --- | --- |
| `large-v3-turbo` | faster-whisper (готовая CT2) | базовая линия, русский |
| `large-v3` | faster-whisper | сравнение качества и скорости |
| BuzzASR/kyrgyz | Hugging Face, дообучение large-v3 | лучший заявленный кыргызский |
| the-cramer-project/AkylAI-STT-small | Hugging Face, Apache-2.0 | маленькая и быстрая |
| UlutSoftLLC/whisper-small-kyrgyz | Hugging Face | маленькая, второй вариант |

**Метрики:**

| Метрика | Как | Порог для решения |
| --- | --- | --- |
| CER / WER по языку | `jiwer`, нормализация: нижний регистр, без пунктуации, `ё→е`; отдельно на ky-, ru- и смешанных участках | выбираем лучшую CER на ky при RTF в пределах; если лучшая CER на ky > 35%, текст становится вспомогательным сигналом (R1), и это фиксируется в записке |
| Скорость (RTF) | секунды обработки / секунды звука, CPU int8 на эталонном ПК; GPU float16 там, где есть | CPU ≤ 0,15 (≥ 6,5× реального времени) для модели по умолчанию |
| Определение языка участка | точность ru/ky на участках эталона | для записки; если < 85%, нужен двухпроходный режим из §5.2 |
| Ключевые слова тостов | доля найденных «тост, куттуктайм, бакыт, ден соолук…» в нечётком поиске по CER ≤ 0,3 | для записки, нужна правилам §6 |
| Пословные метки времени | есть ли и насколько точны (сравнить 20 слов вручную) | нужны для фиттера |

**Files:** `spikes/s1_asr/convert_models.py`, `spikes/s1_asr/refs.py`, `spikes/s1_asr/bench_asr.py`, `docs/spikes/S1-asr.md`

- [ ] **Step 1:** `convert_models.py` — конвертировать кыргызские модели в CTranslate2 (`ct2-transformers-converter --quantization int8_float16`); если модель не конвертируется, записать причину и прогонять через `transformers` для сравнения качества (без цифр скорости).
- [ ] **Step 2:** `refs.py` — читает CSV эталона, вырезает участки из исходников в WAV 16 кГц (через `spikes/common/media.py`).
- [ ] **Step 3:** `bench_asr.py --models … --refs …` — Silero VAD → распознавание → метрики таблицы по каждой модели и языку → `write_result("s1", …)`. Тексты расшифровок сохраняются только в `spikes/data/` (это данные людей), в `results/` идут только цифры.
- [ ] **Step 4:** Прогон CPU на Mac (отладка), CPU и GPU на эталонном ПК.
- [ ] **Step 5:** Записка `docs/spikes/S1-asr.md`: таблица моделей × язык × CER/WER/RTF, лицензии, рекомендованная модель по умолчанию и «точная» модель для GPU, нужен ли двухпроходный режим, как это меняет правила моментов §6 и бюджет §5.3.
- [ ] **Step 6: Commit** `spike(s1): ASR benchmark on toi audio and memo`.

### Task 6: S4 — качество плана от модели

**Блокер:** расшифровки 2 тоев из S1, разметка моментов от монтажёра, `ANTHROPIC_API_KEY` в окружении, монтажёр для оценки. Перед написанием кода загрузить skill `claude-api` (актуальные идентификаторы моделей, структурированный вывод, подсчёт токенов).

**Вопросы и пороги (§7):**

| Метрика | Порог |
| --- | --- |
| Валидность ответа по `schema_v0.json` | 100% из 20 запусков (после одного автоматического повтора) |
| Соблюдение `must` / `exclude` до фиттера | для записки (фиттер всё равно чинит); после ручной проверки фиттер-правил — 100% |
| Размер запроса | ≤ 40 тыс. токенов на той (§7.1) |
| Задержка | p95 ≤ 90 с |
| Оценка монтажёра | 3 задания × 2 тоя, оценка 1–5 «можно ли с этого начать монтаж» | средняя ≥ 3,5 |
| Сравнение моделей | `claude-sonnet-5-5` (по умолчанию) и `claude-haiku-5-5` на тех же запросах: качество / задержка / цена | решение, какую модель ставить в plan-service |

**Files:** `spikes/s4_plan/build_request.py`, `spikes/s4_plan/prompt_v0.md`, `spikes/s4_plan/schema_v0.json`, `spikes/s4_plan/run_plan.py`, `spikes/s4_plan/score_plan.py`, `docs/spikes/S4-plan.md`

- [ ] **Step 1:** `build_request.py` — собирает `PlanRequest` §7.1 из разметки моментов и расшифровок S1 (обрезка текста 600/160 символов). Проверка приватности: в JSON нет путей, имён файлов и метаданных устройства. Есть тест на это в `tests/spikes/`.
- [ ] **Step 2:** `prompt_v0.md` и `schema_v0.json` — по §7.2 (`items`, `reserve`, `music`, причины на ru и ky).
- [ ] **Step 3:** `run_plan.py request.json --model … --runs 10` → ответы и метрики (токены, задержка, валидность, must/exclude) → `write_result("s4", …)`. Ответы модели (содержат куски речи) сохраняются в `spikes/data/`.
- [ ] **Step 4:** `score_plan.py` печатает план в читаемом виде («01:12:40 Тост отца · 41 с · почему: …») для показа монтажёру и сохраняет его оценки в CSV.
- [ ] **Step 5:** Сессия с монтажёром: 3 задания на той («Клип 3 мин», «Только тосты», «Тизер 60 с 9:16») × 2 тоя.
- [ ] **Step 6:** Записка `docs/spikes/S4-plan.md`: цифры, оценки, типичные ошибки модели, выбранная модель, что поменять в промпте и в фиттере §7.3.
- [ ] **Step 7: Commit** `spike(s4): plan quality evaluation and memo`.

### Task 7: Сводка решений и правки архитектуры

**Files:** `docs/spikes/phase0-decisions.md`; Modify: `docs/architecture.md` (§0, §2, §5.2–5.3, §7.6, §8.2, §9, §11 таблица лицензий, §13)

- [ ] **Step 1:** `phase0-decisions.md` — одна таблица «вопрос → решение → цифры → ссылка на записку» по S1–S5 и список рисков, которые остались открытыми.
- [ ] **Step 2:** Внести решения в `docs/architecture.md`: модель речи, вариант встраивания mpv и сборка, параметры proxy и метрик, размер чанка и кодировщики, модель для plan-service, обновлённый бюджет времени анализа.
- [ ] **Step 3:** Отметить в §13 фазу 0 как пройденную и уточнить сроки фаз 2–5, если цифры их меняют.
- [ ] **Step 4: Commit** `docs: phase 0 decisions and architecture updates`.

---

## Что делать, если спайк провалился

| Спайк | Провал | Запасной путь |
| --- | --- | --- |
| S1 | лучшая CER на ky > 35% | тосты находим по структуре звука; текст — подсказка для модели; ключевые слова — нечётким поиском; «точная расшифровка» — опция на GPU; заложить сбор данных и дообучение на собственных записях как отдельный проект |
| S2 | mpv не бесшовен или нет LGPL-сборки | QtMultimedia с двумя плеерами; или своя сборка libmpv `-Dgpl=false` в CI |
| S3 | < 12× без GPU | proxy 360p, метрики 2 к/с, распознавание только участков без музыки; честный ETA 40–50 мин и уведомление |
| S4 | средняя оценка < 3,5 | больше логики в фиттере (структура «выход → тосты → танцы → финал» по правилам), модель только выбирает и упорядочивает; повторить S4 с промптом v1 |
| S5 | дрейф или рассинхрон | рендер одним `filter_complex` для клипов до 10 мин; чанки только для возобновления длинных фильмов |
