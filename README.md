# Той Студия (mojo_studio)

Десктопная программа для Windows, которая автоматически монтирует тойские видео: свадьбы, тои, юбилеи. Анализ видео идёт локально, облачная модель получает только текст и составляет план монтажа, монтажёр правит черновик и экспортирует клип. Реализация — Python 3.12 + PySide6.

- Схема MVP: [toy-video-mvp-architecture.svg](toy-video-mvp-architecture.svg)
- Архитектура, риски и дорожная карта: [docs/architecture.md](docs/architecture.md)
- План фазы 1 «Фундамент»: [docs/superpowers/plans/2026-11-06-toy-studio-foundation.md](docs/superpowers/plans/2026-11-06-toy-studio-foundation.md)
- Дизайн-система: https://claude.ai/artifact/CL1yyrtm3XzrovRgnNQRxw

## Запуск

Нужно:

- Python 3.12;
- ffmpeg и ffprobe версии 7 или новее — в `PATH` или в папке, указанной в переменной `TOYSTUDIO_FFMPEG_DIR`.

```sh
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip install -e ".[dev]"
python -m toystudio.app.main
```

Где лежат данные:

- проекты — `%USERPROFILE%\Videos\Той Студия` (macOS: `~/Movies/Той Студия`, Linux: `~/Videos/Той Студия`);
- список проектов и скорости анализа — `%APPDATA%\ToyStudio\app.db` (вне Windows: `~/.toystudio/app.db`);
- `TOYSTUDIO_HOME=<папка>` кладёт и проекты (`<папка>/projects`), и `app.db` в одну папку — для тестов и переносного запуска.

Тесты: `pytest` (сквозной тест `tests/app/test_flow_phase2.py` запускает настоящий анализ и требует ffmpeg 7+).
