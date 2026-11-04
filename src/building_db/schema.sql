-- Step 1 schema. Table and column names follow the design doc v0.6.
-- Calculated values (area, length, quantities) are never stored.

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name       TEXT NOT NULL,
    source_path       TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    imported_at       TEXT NOT NULL,          -- UTC, ISO 8601
    reader            TEXT NOT NULL,          -- e.g. "ezdxf 1.4.5 recover"
    recover_mode      INTEGER NOT NULL,       -- 1 if read in ezdxf recover mode
    dxf_version       TEXT,
    encoding          TEXT NOT NULL,
    encoding_assumed  INTEGER NOT NULL,       -- 1 if $DWGCODEPAGE was missing
    repairs           TEXT NOT NULL,          -- JSON list of repair messages
    duplicate_handles TEXT NOT NULL,          -- JSON list of handles seen twice
    has_duplicate_handles INTEGER NOT NULL,
    unit              TEXT NOT NULL,
    unit_assumed      INTEGER NOT NULL,
    origin_x          REAL NOT NULL,          -- raw DXF coordinates of X1/Y1
    origin_y          REAL NOT NULL,
    review_flags      TEXT NOT NULL           -- JSON list of items a person should check
);

CREATE TABLE IF NOT EXISTS grids (
    grid_id   TEXT PRIMARY KEY,
    label     TEXT NOT NULL UNIQUE,           -- key on the drawing, e.g. X1
    direction TEXT NOT NULL CHECK (direction IN ('X', 'Y')),
    position  REAL NOT NULL
);

-- last_seen_batch: the import batch whose drawing last showed the element.
-- An element missing from a later drawing stays in the DB with its old batch.

CREATE TABLE IF NOT EXISTS nodes (
    node_id         TEXT PRIMARY KEY,
    x               REAL NOT NULL,
    y               REAL NOT NULL,
    last_seen_batch INTEGER REFERENCES import_batches(batch_id)
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id         TEXT PRIMARY KEY,
    node_start      TEXT NOT NULL REFERENCES nodes(node_id),
    node_end        TEXT NOT NULL REFERENCES nodes(node_id),
    wall_policy     TEXT NOT NULL DEFAULT 'default_wall'
                    CHECK (wall_policy IN ('default_wall', 'no_wall', 'undecided')),
    last_seen_batch INTEGER REFERENCES import_batches(batch_id),
    UNIQUE (node_start, node_end)
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id         TEXT PRIMARY KEY,
    room_no         TEXT,                     -- key on the drawing, e.g. 101
    name            TEXT,                     -- key on the drawing
    ceiling_height  INTEGER,                  -- mm above FL
    last_seen_batch INTEGER REFERENCES import_batches(batch_id)
);

CREATE TABLE IF NOT EXISTS room_boundaries (
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    edge_id TEXT NOT NULL REFERENCES edges(edge_id),
    seq     INTEGER NOT NULL,
    side    TEXT NOT NULL CHECK (side IN ('A', 'B')),  -- A: left of the edge direction
    PRIMARY KEY (room_id, seq)
);

CREATE TABLE IF NOT EXISTS wall_types (
    type_id            TEXT PRIMARY KEY,      -- never changes
    type_code          TEXT NOT NULL UNIQUE,  -- key on the drawing, e.g. WT-3
    name               TEXT,
    core               TEXT,
    core_thickness_mm  INTEGER,
    board_thickness_mm REAL,
    boards_per_side    INTEGER,
    total_thickness_mm INTEGER,
    role               TEXT
);

CREATE TABLE IF NOT EXISTS walls (
    wall_id          TEXT PRIMARY KEY,
    follow_edge      INTEGER NOT NULL DEFAULT 1,
    wall_type_id     TEXT REFERENCES wall_types(type_id),
    adopted_geometry TEXT NOT NULL,           -- JSON [[x1, y1], [x2, y2]] of the wall center
    observed_geometry TEXT NOT NULL,          -- same format, as seen on the latest drawing
    last_seen_batch  INTEGER REFERENCES import_batches(batch_id)
);

-- Why each element of a new version was (or was not) taken to be an element
-- of the previous version. A history per import batch, never overwritten.
-- moved_mm is a fact of that import, not a value recomputed from the current
-- state (SPEC.md 3), so it is stored.
CREATE TABLE IF NOT EXISTS match_log (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id       INTEGER NOT NULL REFERENCES import_batches(batch_id),
    target_table   TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN
                   ('match', 'moved', 'added_candidate', 'missing_candidate', 'undecided')),
    rule           TEXT,                      -- e.g. same_position, connectivity, wall_span
    anchors        TEXT,                      -- JSON list of element IDs the match rested on
    moved_mm       REAL,
    candidates     TEXT,                      -- JSON list of possible previous IDs, if undecided
    review         INTEGER NOT NULL DEFAULT 0 -- 1 if handed to a person
);

CREATE TABLE IF NOT EXISTS wall_edge_spans (
    wall_id  TEXT NOT NULL REFERENCES walls(wall_id),
    edge_id  TEXT NOT NULL REFERENCES edges(edge_id),
    seq      INTEGER NOT NULL,
    reversed INTEGER NOT NULL,                -- 1 if the wall runs against the edge
    PRIMARY KEY (wall_id, seq)
);

CREATE TABLE IF NOT EXISTS field_provenance (
    target_table TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    field        TEXT NOT NULL,
    origin       TEXT NOT NULL
                 CHECK (origin IN ('designer', 'cad_import', 'ai_extract', 'ai_infer', 'default')),
    source       TEXT NOT NULL,               -- file and layer, or how a person gave it
    batch_id     INTEGER REFERENCES import_batches(batch_id),
    PRIMARY KEY (target_table, target_id, field)
);
