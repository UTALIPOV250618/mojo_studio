CREATE TABLE project (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    event_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE media (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('video', 'music')),
    path TEXT NOT NULL,
    copied INTEGER NOT NULL DEFAULT 0,
    size INTEGER,
    duration_us INTEGER,
    fps REAL,
    width INTEGER,
    height INTEGER,
    rotation INTEGER,
    codec TEXT,
    hdr INTEGER,
    creation_time TEXT,
    camera_id TEXT,
    sync_offset_us INTEGER,
    sync_confidence REAL,
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'corrupt', 'missing'))
);
CREATE TABLE stage_run (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped')),
    progress REAL,
    checkpoint TEXT,
    error_code TEXT,
    finished_at TEXT,
    version INTEGER,
    PRIMARY KEY (media_id, stage)
);
CREATE TABLE transcript_segment (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    lang TEXT,
    text TEXT NOT NULL,
    words_json TEXT,
    avg_logprob REAL,
    no_speech_prob REAL
);
CREATE TABLE audio_event (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('speech', 'applause', 'music', 'cheer', 'laughter')),
    score REAL
);
CREATE TABLE scene_cut (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    at_us INTEGER NOT NULL,
    score REAL
);
CREATE TABLE moment (
    id INTEGER PRIMARY KEY,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    score REAL,
    quality_label TEXT,
    transcript_excerpt TEXT,
    user_text TEXT,
    mark TEXT NOT NULL DEFAULT 'none' CHECK (mark IN ('none', 'must', 'exclude')),
    group_id INTEGER
);
CREATE TABLE cut (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    aspect TEXT NOT NULL,
    target_duration_us INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE task (
    id INTEGER PRIMARY KEY,
    cut_id TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
    text TEXT,
    chips_json TEXT,
    music_ids_json TEXT,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft', 'queued', 'sent', 'done', 'failed'))
);
CREATE TABLE plan (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    prompt_version TEXT,
    request_json TEXT,
    response_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE timeline_snapshot (
    cut_id TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (cut_id, seq)
);
CREATE TABLE export_job (
    id INTEGER PRIMARY KEY,
    cut_id TEXT NOT NULL REFERENCES cut(id) ON DELETE CASCADE,
    preset TEXT,
    path TEXT,
    state TEXT,
    done_chunks INTEGER,
    total_chunks INTEGER,
    encoder TEXT,
    error_code TEXT
);
CREATE TABLE job (
    id TEXT PRIMARY KEY,
    kind TEXT,
    state TEXT NOT NULL,
    progress REAL,
    error_code TEXT,
    updated_at TEXT
);
