-- Rebuild moment with AUTOINCREMENT so ids are never reused (ruling R23).
CREATE TABLE moment_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    score REAL,
    quality_label TEXT,
    transcript_excerpt TEXT,
    user_text TEXT,
    mark TEXT NOT NULL DEFAULT 'none' CHECK (mark IN ('none', 'must', 'exclude')),
    group_id INTEGER,
    reasons_json TEXT,
    preview TEXT,
    camera_offset_us INTEGER
);
INSERT INTO moment_new(id, media_id, type, start_us, end_us, score, quality_label,
    transcript_excerpt, user_text, mark, group_id)
    SELECT id, media_id, type, start_us, end_us, score, quality_label,
    transcript_excerpt, user_text, mark, group_id FROM moment;
DROP TABLE moment;
ALTER TABLE moment_new RENAME TO moment;
CREATE TABLE speech_region (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    prob REAL
);
CREATE INDEX audio_event_media ON audio_event(media_id);
CREATE INDEX speech_region_media ON speech_region(media_id);
CREATE INDEX moment_media ON moment(media_id);
