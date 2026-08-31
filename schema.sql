-- Postgres-flavored DDL mirroring models.py. Documentation / manual-psql path;
-- the primary, cross-database way to create + seed these tables is `python -m db.seed`.

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    owner_email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'escalation_status') THEN
        CREATE TYPE escalation_status AS ENUM (
            'pending_triage',
            'not_escalation',
            'pending_approval',
            'approved',
            'rejected',
            'sent',
            'failed',
            'archived'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS escalations (
    id SERIAL PRIMARY KEY,
    gmail_message_id TEXT NOT NULL UNIQUE,
    gmail_thread_id TEXT,
    received_at TIMESTAMPTZ,
    sender TEXT,
    subject TEXT,
    raw_body TEXT,

    is_genuine_escalation BOOLEAN,
    severity TEXT,
    urgency_notes TEXT,
    key_facts TEXT,

    summary TEXT,

    routed_team TEXT,
    recommended_action TEXT,
    owner_email TEXT,

    status escalation_status NOT NULL DEFAULT 'pending_triage',
    approver_notes TEXT,
    approved_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    error TEXT,

    thread_checkpoint_id TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO teams (name, description, owner_email) VALUES
    ('Engineering', 'Handles product bugs, outages, infrastructure, and technical failures.', 'engineering@example.com'),
    ('Product', 'Handles feature requests, product strategy, and roadmap-related escalations.', 'product@example.com'),
    ('Sales', 'Handles customer deal issues, contract negotiations, and revenue-impacting escalations.', 'sales@example.com'),
    ('Customer Support', 'Handles individual customer complaints, service quality, and support ticket escalations.', 'support@example.com'),
    ('Legal', 'Handles compliance, contracts, disputes, regulatory, and litigation matters.', 'legal@example.com'),
    ('Finance', 'Handles billing disputes, payment issues, refunds, and financial reporting concerns.', 'finance@example.com'),
    ('HR', 'Handles employee relations, workplace conduct, and internal personnel escalations.', 'hr@example.com'),
    ('Security/Trust', 'Handles security incidents, data breaches, fraud, and trust & safety issues.', 'security@example.com')
ON CONFLICT (name) DO NOTHING;
