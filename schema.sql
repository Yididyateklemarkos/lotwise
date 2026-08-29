-- Lotwise — PostgreSQL schema
-- Mirrors models.py exactly. db.create_all() will do this automatically on
-- first run against DATABASE_URL, but keep this around for manual setup,
-- backups, or inspecting the schema without spinning up the app.

CREATE TABLE IF NOT EXISTS admin_users (
    id              SERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads (
    id                  SERIAL PRIMARY KEY,
    lead_type           VARCHAR(20) NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'new',

    name                VARCHAR(255) NOT NULL,
    company_name        VARCHAR(255),
    email               VARCHAR(255) NOT NULL,
    phone               VARCHAR(50),
    country             VARCHAR(100),

    category            VARCHAR(100),
    title               VARCHAR(255),
    description         TEXT,
    quantity_note       VARCHAR(120),
    origin_country      VARCHAR(100),
    grade_spec          VARCHAR(255),
    needed_by           DATE,
    preferred_time_note TEXT,

    admin_note          TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_leads_lead_type ON leads (lead_type);
CREATE INDEX IF NOT EXISTS ix_leads_status ON leads (status);

CREATE TABLE IF NOT EXISTS lead_photos (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    file_path   VARCHAR(500) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Constrain lead_type / status to known values. Safe to skip if you'd
-- rather enforce this only in the app layer (models.py LEAD_TYPES/LEAD_STATUSES).
ALTER TABLE leads
    DROP CONSTRAINT IF EXISTS chk_leads_lead_type,
    ADD CONSTRAINT chk_leads_lead_type
        CHECK (lead_type IN ('sourcing', 'consultation', 'meeting', 'supply', 'contact'));

ALTER TABLE leads
    DROP CONSTRAINT IF EXISTS chk_leads_status,
    ADD CONSTRAINT chk_leads_status
        CHECK (status IN ('new', 'contacted', 'in_progress', 'closed'));
