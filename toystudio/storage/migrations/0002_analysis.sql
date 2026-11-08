ALTER TABLE media ADD COLUMN bit_rate INTEGER;
ALTER TABLE media ADD COLUMN has_audio INTEGER;
ALTER TABLE media ADD COLUMN has_video INTEGER;
ALTER TABLE media ADD COLUMN added_at TEXT;
CREATE TABLE quality_interval (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('dark', 'shaky', 'blurry'))
);
-- No FK on media_id: project-level stages (sync) use media_id = 0.
-- remove_media deletes a media's stage rows explicitly.
CREATE TABLE stage_run_new (
    media_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped', 'interrupted')),
    progress REAL,
    checkpoint TEXT,
    error_code TEXT,
    finished_at TEXT,
    version INTEGER,
    PRIMARY KEY (media_id, stage)
);
INSERT INTO stage_run_new(media_id, stage, state, progress, checkpoint, error_code, finished_at, version)
    SELECT media_id, stage, state, progress, checkpoint, error_code, finished_at, version FROM stage_run;
DROP TABLE stage_run;
ALTER TABLE stage_run_new RENAME TO stage_run;
CREATE INDEX scene_cut_media ON scene_cut(media_id);
