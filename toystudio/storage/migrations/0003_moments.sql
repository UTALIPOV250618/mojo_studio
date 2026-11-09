ALTER TABLE moment ADD COLUMN reasons_json TEXT;
ALTER TABLE moment ADD COLUMN preview TEXT;
ALTER TABLE moment ADD COLUMN camera_offset_us INTEGER;
CREATE TABLE speech_region (
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    start_us INTEGER NOT NULL,
    end_us INTEGER NOT NULL,
    prob REAL
);
CREATE INDEX audio_event_media ON audio_event(media_id);
CREATE INDEX speech_region_media ON speech_region(media_id);
CREATE INDEX moment_media ON moment(media_id);
