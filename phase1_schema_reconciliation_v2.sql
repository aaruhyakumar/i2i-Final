-- ============================================================================
-- Ink2Insights — Phase 1: Additive Schema Reconciliation (Version 2)
-- ============================================================================
-- Document Version: 2.0.0
-- Target: Supabase PostgreSQL 15+
-- Architecture: Single Primary Operational Store (Backend-Only Access)
--
-- Corrections Applied in V2:
--   1. Data Type Parity: Columns on public.records aligned to TEXT DEFAULT '[]'
--      and INTEGER DEFAULT 1 to match existing adult_records, paediatric_records,
--      and SQLite types exactly, avoiding type discrepancies between parent and child.
--   2. Index Pruning & Realignment:
--      - REMOVED idx_records_form_family (form_family is never queried in WHERE clauses).
--      - REMOVED idx_records_approved_by (approved_by is updated by PK, never filtered in SELECT).
--      - REMOVED idx_records_locked_by (lock checks query by PK id, never WHERE locked_by = ?).
--      - REMOVED idx_doctors_is_active (doctors queried by unique auth_id/email; is_active has ~0 selectivity).
--      - ADDED composite index idx_records_camp_status (matches actual camp record filtering query pattern).
--   3. Explicit comments and automated verification asserts.
--
-- Safety Guarantees:
--   - STRICTLY ADDITIVE: Uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
--   - IDEMPOTENT: Re-running this script produces identical final state without errors.
--   - ZERO DATA LOSS: Existing rows in Supabase are untouched.
--   - TRANSACTION PROTECTED: All DDL executes inside a single BEGIN ... COMMIT block.
-- ============================================================================

BEGIN;

-- ============================================================================
-- 1. RECONCILE public.records (15 Missing Operational Columns)
-- ============================================================================
-- Column types and defaults strictly match existing adult_records, paediatric_records,
-- and SQLite application logic.

ALTER TABLE public.records
    -- Audit & Concurrency Versioning
    ADD COLUMN IF NOT EXISTS edit_history TEXT DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS confidence_scores TEXT DEFAULT '{}',

    -- Intake Consent
    ADD COLUMN IF NOT EXISTS consent_given INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS consent_signature TEXT DEFAULT NULL,

    -- Locking & Clinical Approval
    ADD COLUMN IF NOT EXISTS locked_by TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS approved_by TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,

    -- AI Extraction Baseline
    ADD COLUMN IF NOT EXISTS original_ai_extraction TEXT DEFAULT NULL,

    -- Quality Control & QA Overrides
    ADD COLUMN IF NOT EXISTS quality_warning_bypassed TEXT DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS quality_bypassed INTEGER DEFAULT 0,

    -- Template Classification & Form Family
    ADD COLUMN IF NOT EXISTS template_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS template_version TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS form_family TEXT DEFAULT 'adult';

-- Documentation
COMMENT ON COLUMN public.records.edit_history IS 'Serialized JSON array of audit diffs created during human review';
COMMENT ON COLUMN public.records.version IS 'Optimistic locking concurrency version counter';
COMMENT ON COLUMN public.records.confidence_scores IS 'Serialized JSON map of field-level AI confidence ratings';
COMMENT ON COLUMN public.records.consent_given IS 'Patient/Guardian consent captured (1=yes, 0=no)';
COMMENT ON COLUMN public.records.consent_signature IS 'Base64 signature string or consent reference';
COMMENT ON COLUMN public.records.locked_by IS 'Doctor/Staff identifier holding exclusive editing lock';
COMMENT ON COLUMN public.records.locked_at IS 'Timestamp when active edit lock was acquired';
COMMENT ON COLUMN public.records.approved_by IS 'Doctor identifier who clinically approved the record';
COMMENT ON COLUMN public.records.approved_at IS 'Timestamp of clinical sign-off';
COMMENT ON COLUMN public.records.original_ai_extraction IS 'Serialized raw JSON extraction from Claude before review';
COMMENT ON COLUMN public.records.quality_warning_bypassed IS 'Serialized JSON list of acknowledged image quality warnings';
COMMENT ON COLUMN public.records.quality_bypassed IS 'Flag indicating image QA checks were overridden (1=yes, 0=no)';
COMMENT ON COLUMN public.records.template_id IS 'Official form template code (e.g. ASF-D&A-PCS-9.3)';
COMMENT ON COLUMN public.records.template_version IS 'Template revision version string';
COMMENT ON COLUMN public.records.form_family IS 'Clinical discriminator: adult or paediatric';


-- ============================================================================
-- 2. RECONCILE public.doctors (Account Status)
-- ============================================================================
-- Uses BOOLEAN DEFAULT true to match modern PostgreSQL / Supabase conventions
-- while remaining compatible with Python boolean conversions.
ALTER TABLE public.doctors
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.doctors.is_active IS 'Active status flag for clinical user profile';


-- ============================================================================
-- 3. RECONCILE public.communities (Demo Camp Expiry)
-- ============================================================================
-- Supports legacy demo community codes evaluated by database.get_community_by_code().
ALTER TABLE public.communities
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;

COMMENT ON COLUMN public.communities.expires_at IS 'Optional expiration timestamp for temporary demo community camps';


-- ============================================================================
-- 4. REALIGNED QUERY-DRIVEN PERFORMANCE INDEXES
-- ============================================================================
-- Pruned useless single-column indexes on low-cardinality/unfiltered columns.
-- Added composite indexes matching the actual query: WHERE camp_id = ? AND status = ?

CREATE INDEX IF NOT EXISTS idx_records_camp_status 
    ON public.records(camp_id, status);

CREATE INDEX IF NOT EXISTS idx_adult_records_camp_status 
    ON public.adult_records(camp_id, status);

CREATE INDEX IF NOT EXISTS idx_paediatric_records_camp_status 
    ON public.paediatric_records(camp_id, status);


-- ============================================================================
-- 5. VERIFICATION & ASSERTION BLOCK
-- ============================================================================
DO $$
DECLARE
    missing_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO missing_count
    FROM (
        VALUES 
            ('edit_history'), ('version'), ('confidence_scores'), ('consent_given'),
            ('consent_signature'), ('locked_by'), ('locked_at'), ('approved_by'),
            ('approved_at'), ('original_ai_extraction'), ('quality_warning_bypassed'),
            ('quality_bypassed'), ('template_id'), ('template_version'), ('form_family')
    ) AS expected(col_name)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' 
          AND table_name = 'records' 
          AND column_name = expected.col_name
    );

    IF missing_count > 0 THEN
        RAISE EXCEPTION 'Schema reconciliation v2 failed: % columns missing from public.records', missing_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'doctors' AND column_name = 'is_active'
    ) THEN
        RAISE EXCEPTION 'Schema reconciliation v2 failed: is_active missing from public.doctors';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'communities' AND column_name = 'expires_at'
    ) THEN
        RAISE EXCEPTION 'Schema reconciliation v2 failed: expires_at missing from public.communities';
    END IF;

    RAISE NOTICE 'SUCCESS: Schema reconciliation v2 verified. 100%% column parity achieved.';
END $$;

COMMIT;
-- ============================================================================
-- End of Phase 1 Schema Reconciliation V2
-- ============================================================================
