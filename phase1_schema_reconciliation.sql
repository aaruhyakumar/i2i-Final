-- ============================================================================
-- Ink2Insights — Phase 1: Supabase PostgreSQL Additive Schema Reconciliation
-- ============================================================================
-- Purpose:
--   Add missing operational columns to remote Supabase PostgreSQL to achieve
--   100% schema parity with SQLite before data backfill and operational cutover.
--
-- Tables Reconciled:
--   1. public.records       (15 missing operational and clinical metadata columns)
--   2. public.doctors       (1 missing column: is_active)
--   3. public.communities   (1 missing column: expires_at)
--
-- Safety Guarantees:
--   - STRICTLY ADDITIVE: No tables or columns are dropped or altered destructively.
--   - IDEMPOTENT: Uses IF NOT EXISTS clauses throughout.
--   - ZERO DATA LOSS: Existing rows in Supabase are preserved untouched.
--   - TRANSACTION PROTECTED: All changes execute within a single atomic transaction.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Reconcile public.records (15 Missing Operational Columns)
-- ----------------------------------------------------------------------------
-- Matches the SQLite records table schema and existing adult_records / 
-- paediatric_records definitions in Supabase.

ALTER TABLE public.records
    -- Audit & Concurrency
    ADD COLUMN IF NOT EXISTS edit_history TEXT DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS confidence_scores TEXT DEFAULT '{}',

    -- Consent & Legal Intake
    ADD COLUMN IF NOT EXISTS consent_given INTEGER DEFAULT 1,
    ADD COLUMN IF NOT EXISTS consent_signature TEXT DEFAULT NULL,

    -- Concurrency Lock & Clinical Finalization
    ADD COLUMN IF NOT EXISTS locked_by TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS approved_by TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,

    -- Claude AI Extraction Baseline
    ADD COLUMN IF NOT EXISTS original_ai_extraction TEXT DEFAULT NULL,

    -- Quality Control & Warnings
    ADD COLUMN IF NOT EXISTS quality_warning_bypassed TEXT DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS quality_bypassed INTEGER DEFAULT 0,

    -- Form Classification & Template Versioning
    ADD COLUMN IF NOT EXISTS template_id TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS template_version TEXT DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS form_family TEXT DEFAULT 'adult';

-- Comments for clinical data catalog
COMMENT ON COLUMN public.records.edit_history IS 'JSON array of historical human edits and diffs';
COMMENT ON COLUMN public.records.version IS 'Optimistic locking version counter';
COMMENT ON COLUMN public.records.confidence_scores IS 'JSON map of field-level AI extraction confidence ratings';
COMMENT ON COLUMN public.records.consent_given IS 'Patient/Guardian consent captured (1=yes, 0=no)';
COMMENT ON COLUMN public.records.consent_signature IS 'Base64 signature binary or reference string';
COMMENT ON COLUMN public.records.locked_by IS 'User UUID holding exclusive editing lock';
COMMENT ON COLUMN public.records.locked_at IS 'Timestamp when edit lock was acquired';
COMMENT ON COLUMN public.records.approved_by IS 'Doctor UUID who approved the record';
COMMENT ON COLUMN public.records.approved_at IS 'Timestamp of clinical sign-off';
COMMENT ON COLUMN public.records.original_ai_extraction IS 'Raw Claude JSON extraction prior to human modifications';
COMMENT ON COLUMN public.records.quality_warning_bypassed IS 'JSON list of visual QA warnings acknowledged by operator';
COMMENT ON COLUMN public.records.quality_bypassed IS 'Flag indicating visual quality checks were overridden (1=yes, 0=no)';
COMMENT ON COLUMN public.records.template_id IS 'Form template code (e.g. ASF-D&A-PCS-9.3)';
COMMENT ON COLUMN public.records.template_version IS 'Template revision string';
COMMENT ON COLUMN public.records.form_family IS 'Clinical discriminator: adult or paediatric';


-- ----------------------------------------------------------------------------
-- 2. Reconcile public.doctors (Account Status)
-- ----------------------------------------------------------------------------
-- SQLite stores is_active (INTEGER 1/0). Supabase uses BOOLEAN.
ALTER TABLE public.doctors
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.doctors.is_active IS 'Active status flag for clinical user profile';


-- ----------------------------------------------------------------------------
-- 3. Reconcile public.communities (Demo Camp Lifecycle)
-- ----------------------------------------------------------------------------
-- SQLite stores expires_at for demo camps. Supabase communities requires this.
ALTER TABLE public.communities
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;

COMMENT ON COLUMN public.communities.expires_at IS 'Optional expiration timestamp for temporary demo community camps';


-- ----------------------------------------------------------------------------
-- 4. Justified Performance Indexes
-- ----------------------------------------------------------------------------
-- Only create indexes where clinical workflows frequently filter or join.

-- Fast discrimination between adult and paediatric records
CREATE INDEX IF NOT EXISTS idx_records_form_family 
    ON public.records(form_family);

-- Fast lookup for records pending doctor approval vs approved
CREATE INDEX IF NOT EXISTS idx_records_approved_by 
    ON public.records(approved_by) 
    WHERE approved_by IS NOT NULL;

-- Fast index for active lock contention checks
CREATE INDEX IF NOT EXISTS idx_records_locked_by 
    ON public.records(locked_by) 
    WHERE locked_by IS NOT NULL;

-- Fast filter for active clinical profiles
CREATE INDEX IF NOT EXISTS idx_doctors_is_active 
    ON public.doctors(is_active);


-- ----------------------------------------------------------------------------
-- 5. Verification & Schema Validation
-- ----------------------------------------------------------------------------
-- This query verifies that all 15 columns on public.records now exist.
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
        RAISE EXCEPTION 'Schema reconciliation failed: % columns missing from public.records', missing_count;
    ELSE
        RAISE NOTICE 'SUCCESS: public.records reconciled with 100%% column parity.';
    END IF;
END $$;

COMMIT;
-- ============================================================================
-- End of Phase 1 Schema Reconciliation DDL
-- ============================================================================
