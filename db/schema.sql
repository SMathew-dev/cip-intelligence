-- CIP Intelligence core relational model (PostgreSQL-oriented)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE plants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    timezone text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    parent_asset_id uuid REFERENCES assets(id),
    asset_type text NOT NULL,
    name text NOT NULL,
    external_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (plant_id, name)
);

CREATE TABLE source_systems (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    source_type text NOT NULL,
    name text NOT NULL,
    read_only boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE semantic_tags (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept text NOT NULL UNIQUE,
    canonical_unit text,
    description text
);

CREATE TABLE tag_mappings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id uuid NOT NULL REFERENCES source_systems(id),
    asset_id uuid REFERENCES assets(id),
    source_tag text NOT NULL,
    semantic_tag_id uuid NOT NULL REFERENCES semantic_tags(id),
    source_unit text,
    scale_factor double precision NOT NULL DEFAULT 1,
    offset_value double precision NOT NULL DEFAULT 0,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (source_system_id, source_tag)
);

CREATE TABLE raw_ingestions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id uuid REFERENCES source_systems(id),
    object_uri text,
    sha256 text,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE sensor_readings (
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    semantic_tag_id uuid NOT NULL REFERENCES semantic_tags(id),
    ts timestamptz NOT NULL,
    value_double double precision,
    value_text text,
    canonical_unit text,
    source_unit text,
    quality_code text NOT NULL DEFAULT 'GOOD',
    ingestion_id uuid REFERENCES raw_ingestions(id),
    PRIMARY KEY (semantic_tag_id, ts, plant_id)
);

CREATE TABLE cip_recipes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    name text NOT NULL,
    revision text NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    approval_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (plant_id, name, revision)
);

CREATE TABLE cip_recipe_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recipe_id uuid NOT NULL REFERENCES cip_recipes(id),
    step_order integer NOT NULL,
    phase_type text NOT NULL,
    limits jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (recipe_id, step_order)
);

CREATE TABLE cip_cycles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid NOT NULL REFERENCES assets(id),
    recipe_id uuid REFERENCES cip_recipes(id),
    start_ts timestamptz NOT NULL,
    end_ts timestamptz,
    reconstruction_method text NOT NULL,
    reconstruction_confidence double precision,
    status text NOT NULL DEFAULT 'ANALYZING',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE cip_phases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id uuid NOT NULL REFERENCES cip_cycles(id),
    phase_type text NOT NULL,
    phase_order integer NOT NULL,
    start_ts timestamptz NOT NULL,
    end_ts timestamptz NOT NULL,
    source_method text NOT NULL,
    confidence double precision,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (cycle_id, phase_order)
);

CREATE TABLE production_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    product_code text,
    batch_ref text,
    start_ts timestamptz NOT NULL,
    end_ts timestamptz,
    context jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE qa_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    cycle_id uuid REFERENCES cip_cycles(id),
    sample_ts timestamptz NOT NULL,
    test_type text NOT NULL,
    result_numeric double precision,
    result_text text,
    unit text,
    disposition text,
    source_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE maintenance_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    event_ts timestamptz NOT NULL,
    event_type text NOT NULL,
    description text,
    confirmed_failure_mode text,
    source_ref text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE operator_annotations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    cycle_id uuid REFERENCES cip_cycles(id),
    event_ts timestamptz NOT NULL,
    annotation_type text NOT NULL,
    note text,
    structured_value jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE data_quality_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid NOT NULL REFERENCES plants(id),
    asset_id uuid REFERENCES assets(id),
    semantic_tag_id uuid REFERENCES semantic_tags(id),
    start_ts timestamptz NOT NULL,
    end_ts timestamptz,
    issue_type text NOT NULL,
    severity text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE analysis_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cycle_id uuid NOT NULL REFERENCES cip_cycles(id),
    engine_version text NOT NULL,
    model_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
    recipe_revision text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    data_coverage double precision,
    status text NOT NULL
);

CREATE TABLE findings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id uuid NOT NULL REFERENCES analysis_runs(id),
    finding_code text NOT NULL,
    finding_class text NOT NULL, -- MEASURED / DERIVED / INFERRED / UNKNOWN
    severity text NOT NULL,
    title text NOT NULL,
    conclusion text NOT NULL,
    confidence double precision,
    evidence_quality double precision,
    alternatives jsonb NOT NULL DEFAULT '[]'::jsonb,
    recommendation text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE finding_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id uuid NOT NULL REFERENCES findings(id),
    evidence_type text NOT NULL,
    source_ref text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- M10 operational governance / audit schema
CREATE TABLE IF NOT EXISTS app_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject text UNIQUE NOT NULL,
    display_name text,
    role text NOT NULL CHECK (role IN ('viewer','engineer','qa','admin')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_ts timestamptz NOT NULL DEFAULT now(),
    actor_subject text NOT NULL,
    actor_role text NOT NULL,
    request_id text NOT NULL,
    method text NOT NULL,
    path text NOT NULL,
    response_status integer NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_events_ts_idx ON audit_events(event_ts DESC);

CREATE TABLE IF NOT EXISTS background_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    state text NOT NULL CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
    attempts integer NOT NULL DEFAULT 0,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS connector_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plant_id uuid REFERENCES plants(id),
    name text UNIQUE NOT NULL,
    connector_type text NOT NULL,
    access_mode text NOT NULL DEFAULT 'read_only' CHECK (access_mode = 'read_only'),
    enabled boolean NOT NULL DEFAULT true,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
