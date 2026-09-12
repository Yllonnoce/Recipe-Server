-- Recipe Library schema v1. Applied by db/migrate.py; PRAGMA user_version tracks the number.

CREATE TABLE assets (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,                 -- pdf | thumb | cover | page_image | source_html
    rel_path TEXT NOT NULL,             -- relative to the assets dir
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    mime TEXT NOT NULL,
    page_count INTEGER,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (sha256, kind)
);

CREATE TABLE recipes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    source_url TEXT,
    source_url_norm TEXT,
    source_name TEXT,
    yield_text TEXT,
    servings REAL,
    prep_min INTEGER,
    cook_min INTEGER,
    total_min INTEGER,
    status TEXT NOT NULL DEFAULT 'processing',   -- processing | needs_review | ready | failed
    extraction_method TEXT NOT NULL DEFAULT 'none', -- scraper | llm | manual | none
    confidence REAL,
    pdf_asset_id INTEGER REFERENCES assets(id),
    cover_asset_id INTEGER REFERENCES assets(id),
    thumb_asset_id INTEGER REFERENCES assets(id),
    favorite INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    language TEXT,
    last_page INTEGER,
    page_count INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX ix_recipes_source_url_norm ON recipes(source_url_norm);
CREATE INDEX ix_recipes_status ON recipes(status);
CREATE INDEX ix_recipes_updated ON recipes(updated_at);

CREATE TABLE ingredients (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    group_name TEXT,
    raw_text TEXT NOT NULL,
    quantity REAL,
    quantity_max REAL,
    unit TEXT,
    unit_raw TEXT,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    preparation TEXT,
    optional INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_ingredients_recipe ON ingredients(recipe_id, position);

CREATE TABLE steps (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    group_name TEXT,
    text TEXT NOT NULL,
    minutes INTEGER
);
CREATE INDEX ix_steps_recipe ON steps(recipe_id, position);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'custom',   -- course | cuisine | source | custom
    UNIQUE (name, kind)
);

CREATE TABLE recipe_tags (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (recipe_id, tag_id)
);

CREATE TABLE recipe_text (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    text_source TEXT NOT NULL,          -- layer | ocr | html
    page_texts TEXT NOT NULL            -- JSON list, one string per page
);

CREATE VIRTUAL TABLE recipes_fts USING fts5(
    title, description, ingredients, tags, body,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3'
);

CREATE TABLE recipe_bookmarks (
    id INTEGER PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (recipe_id, page)
);

CREATE TABLE capture_jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,               -- url | upload | watch | printer
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | running | waiting_llm | done | failed | canceled
    stage TEXT,
    stage_log TEXT NOT NULL DEFAULT '[]',
    input_url TEXT,
    input_path TEXT,
    title_hint TEXT,
    ipp_job_id INTEGER,
    recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    asset_id INTEGER REFERENCES assets(id),
    duplicate_of INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    force INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX ix_jobs_state ON capture_jobs(state, next_attempt_at);

CREATE TABLE shopping_lists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE shopping_items (
    id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    checked INTEGER NOT NULL DEFAULT 0,
    manual INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    sources TEXT NOT NULL DEFAULT '[]'  -- JSON [{recipe_id, ingredient_id, scaled_qty}]
);

CREATE TABLE meal_plan_entries (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,                 -- YYYY-MM-DD
    slot TEXT NOT NULL,                 -- breakfast | lunch | dinner | snack | other
    recipe_id INTEGER REFERENCES recipes(id) ON DELETE CASCADE,
    note TEXT,
    servings_override REAL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_plan_date ON meal_plan_entries(date);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL                 -- JSON
);
