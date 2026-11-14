-- Einzige fachliche Tabelle: ein Auftrag ist zugleich das Ergebnis (Abschnitt 9.2).
CREATE TABLE jobs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_id       TEXT NOT NULL,
    title            TEXT,
    status           TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    stage            TEXT,
    progress         REAL NOT NULL DEFAULT 0,
    options          JSONB NOT NULL,
    error            TEXT,
    attempts         SMALLINT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ,
    package_file     TEXT,
    package_size     BIGINT,
    screenshot_count INTEGER
);

-- "neuester Auftrag zu diesem Video"
CREATE INDEX jobs_video_idx ON jobs (youtube_id, created_at DESC);
-- Warteschlange in Reihenfolge created_at
CREATE INDEX jobs_queue_idx ON jobs (created_at) WHERE status = 'queued';
-- Kein zweiter Auftrag für ein Video, solange einer wartet oder läuft (9.3)
CREATE UNIQUE INDEX jobs_one_active_per_video ON jobs (youtube_id) WHERE status IN ('queued', 'running');
