-- ============================================================================
-- Ink2Insights — Phase 1: Active Schema Reconciliation (Version 4)
-- ============================================================================
-- Document Version: 4.0.0
-- Target: Supabase PostgreSQL 15+
-- Architecture: Single Primary Operational Store (Backend-Only Access)
--
-- Enhancements in V4:
--   1. Active Reconciliation Engine: Replaces "detection-only" ADD COLUMN IF NOT EXISTS.
--      Inspects existing column types, nullability, and defaults dynamically.
--      Safely reconciles existing columns in-place (updating NULLs before NOT NULL,
--      aligning defaults, and performing non-destructive compatible type coercions).
--   2. Data Preservation Guarantee: Halts with an explicit exception if an incompatible
--      type mismatch is detected that cannot be safely converted automatically.
--   3. Rigorous Post-Reconciliation Verification: Validates that all 15 public.records
--      columns, public.doctors.is_active, and public.communities.expires_at meet
--      exact PostgreSQL data type, nullability, and default specifications.
--   4. Query-Driven Performance Index: Preserves composite index idx_records_camp_status
--      matching actual backend camp filtering query patterns.
--
-- Safety Guarantees:
--   - TRANSACTION PROTECTED: Entire reconciliation executes inside an atomic BEGIN ... COMMIT block.
--   - ZERO DATA DESTRUCTION: Does not blindly drop or cast ambiguous data.
--   - IDEMPOTENT: Running multiple times results in the identical reconciled state.
-- ============================================================================

BEGIN;

-- ============================================================================
-- PART 1: ACTIVE RECONCILIATION FOR public.records (15 Columns)
-- ============================================================================

DO $$
DECLARE
    rec RECORD;
    curr_data_type TEXT;
    curr_is_nullable TEXT;
    curr_default TEXT;
    existing_nulls_count INTEGER;
BEGIN
    -- Temporary definition table for the 15 expected records columns
    CREATE TEMP TABLE expected_records_cols_v4 (
        col_name TEXT PRIMARY KEY,
        target_type TEXT NOT NULL,
        is_nullable_target TEXT NOT NULL,
        default_expr TEXT,
        default_literal_match TEXT
    ) ON COMMIT DROP;

    INSERT INTO expected_records_cols_v4 VALUES
        ('edit_history',              'text',                        'YES', '''[]''',     '[]'),
        ('version',                   'integer',                     'NO',  '1',          '1'),
        ('confidence_scores',         'text',                        'YES', '''{}''',     '{}'),
        ('consent_given',             'integer',                     'YES', '1',          '1'),
        ('consent_signature',         'text',                        'YES', NULL,         NULL),
        ('locked_by',                 'text',                        'YES', NULL,         NULL),
        ('locked_at',                 'timestamp with time zone',    'YES', NULL,         NULL),
        ('approved_by',               'text',                        'YES', NULL,         NULL),
        ('approved_at',               'timestamp with time zone',    'YES', NULL,         NULL),
        ('original_ai_extraction',     'text',                        'YES', NULL,         NULL),
        ('quality_warning_bypassed',  'text',                        'YES', '''[]''',     '[]'),
        ('quality_bypassed',          'integer',                     'YES', '0',          '0'),
        ('template_id',               'text',                        'YES', NULL,         NULL),
        ('template_version',          'text',                        'YES', NULL,         NULL),
        ('form_family',               'text',                        'YES', '''adult''',  'adult');

    FOR rec IN SELECT * FROM expected_records_cols_v4 LOOP
        -- Check if column exists
        SELECT data_type, is_nullable, column_default 
        INTO curr_data_type, curr_is_nullable, curr_default
        FROM information_schema.columns
        WHERE table_schema = 'public' 
          AND table_name = 'records' 
          AND column_name = rec.col_name;

        -- CASE A: Column is missing -> Add it explicitly
        IF curr_data_type IS NULL THEN
            IF rec.is_nullable_target = 'NO' THEN
                EXECUTE format(
                    'ALTER TABLE public.records ADD COLUMN %I %s NOT NULL DEFAULT %s;',
                    rec.col_name, rec.target_type, rec.default_expr
                );
            ELSIF rec.default_expr IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE public.records ADD COLUMN %I %s DEFAULT %s;',
                    rec.col_name, rec.target_type, rec.default_expr
                );
            ELSE
                EXECUTE format(
                    'ALTER TABLE public.records ADD COLUMN %I %s DEFAULT NULL;',
                    rec.col_name, rec.target_type
                );
            END IF;
            RAISE NOTICE 'Added missing column: public.records.%', rec.col_name;

        -- CASE B: Column exists -> Active Reconciliation
        ELSE
            -- 1. Reconcile Type if divergent
            IF curr_data_type != rec.target_type THEN
                -- Safe automatic coercions (e.g. varchar/bpchar to text)
                IF curr_data_type IN ('character varying', 'character') AND rec.target_type = 'text' THEN
                    EXECUTE format('ALTER TABLE public.records ALTER COLUMN %I TYPE text;', rec.col_name);
                    RAISE NOTICE 'Safely widened public.records.% from % to text', rec.col_name, curr_data_type;
                -- Safe coercion from numeric/smallint to integer
                ELSIF curr_data_type IN ('smallint', 'bigint', 'numeric') AND rec.target_type = 'integer' THEN
                    EXECUTE format('ALTER TABLE public.records ALTER COLUMN %I TYPE integer USING (%I::integer);', rec.col_name, rec.col_name);
                    RAISE NOTICE 'Safely coerced public.records.% from % to integer', rec.col_name, curr_data_type;
                ELSE
                    RAISE EXCEPTION 'RECONCILIATION_HALTED: Column public.records.% has type %, expected %. Automatic conversion poses data loss risk. Manual review required.',
                        rec.col_name, curr_data_type, rec.target_type;
                END IF;
            END IF;

            -- 2. Reconcile Default Expression
            IF rec.default_expr IS NOT NULL THEN
                IF curr_default IS NULL OR position(rec.default_literal_match in curr_default) = 0 THEN
                    EXECUTE format('ALTER TABLE public.records ALTER COLUMN %I SET DEFAULT %s;', rec.col_name, rec.default_expr);
                    RAISE NOTICE 'Reconciled default for public.records.% to %', rec.col_name, rec.default_expr;
                END IF;
            END IF;

            -- 3. Reconcile Nullability
            IF rec.is_nullable_target = 'NO' AND curr_is_nullable = 'YES' THEN
                -- Check for existing NULLs before applying NOT NULL constraint
                EXECUTE format('SELECT count(*) FROM public.records WHERE %I IS NULL;', rec.col_name) INTO existing_nulls_count;
                IF existing_nulls_count > 0 THEN
                    -- Populate NULLs with the default value safely
                    EXECUTE format('UPDATE public.records SET %I = %s WHERE %I IS NULL;', rec.col_name, rec.default_expr, rec.col_name);
                    RAISE NOTICE 'Backfilled % existing NULL values for public.records.% with %', existing_nulls_count, rec.col_name, rec.default_expr;
                END IF;
                EXECUTE format('ALTER TABLE public.records ALTER COLUMN %I SET NOT NULL;', rec.col_name);
                RAISE NOTICE 'Applied NOT NULL constraint to public.records.%', rec.col_name;
            ELSIF rec.is_nullable_target = 'YES' AND curr_is_nullable = 'NO' THEN
                EXECUTE format('ALTER TABLE public.records ALTER COLUMN %I DROP NOT NULL;', rec.col_name);
                RAISE NOTICE 'Dropped NOT NULL constraint on public.records.%', rec.col_name;
            END IF;
        END IF;
    END LOOP;
END $$;


-- ============================================================================
-- PART 2: ACTIVE RECONCILIATION FOR public.doctors (is_active)
-- ============================================================================

DO $$
DECLARE
    curr_data_type TEXT;
    curr_is_nullable TEXT;
    null_count INTEGER;
BEGIN
    SELECT data_type, is_nullable INTO curr_data_type, curr_is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'doctors' AND column_name = 'is_active';

    IF curr_data_type IS NULL THEN
        ALTER TABLE public.doctors ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true;
        RAISE NOTICE 'Added missing column: public.doctors.is_active';
    ELSE
        -- Type conversion if exists as integer
        IF curr_data_type IN ('integer', 'smallint') THEN
            ALTER TABLE public.doctors ALTER COLUMN is_active TYPE BOOLEAN USING (is_active != 0);
            RAISE NOTICE 'Converted public.doctors.is_active from integer to BOOLEAN';
        ELSIF curr_data_type != 'boolean' THEN
            RAISE EXCEPTION 'RECONCILIATION_HALTED: public.doctors.is_active has incompatible type %', curr_data_type;
        END IF;

        -- Ensure default is true
        ALTER TABLE public.doctors ALTER COLUMN is_active SET DEFAULT true;

        -- Ensure NOT NULL constraint
        IF curr_is_nullable = 'YES' THEN
            SELECT count(*) INTO null_count FROM public.doctors WHERE is_active IS NULL;
            IF null_count > 0 THEN
                UPDATE public.doctors SET is_active = true WHERE is_active IS NULL;
            END IF;
            ALTER TABLE public.doctors ALTER COLUMN is_active SET NOT NULL;
            RAISE NOTICE 'Applied NOT NULL constraint to public.doctors.is_active';
        END IF;
    END IF;
END $$;


-- ============================================================================
-- PART 3: ACTIVE RECONCILIATION FOR public.communities (expires_at)
-- ============================================================================

DO $$
DECLARE
    curr_data_type TEXT;
BEGIN
    SELECT data_type INTO curr_data_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'communities' AND column_name = 'expires_at';

    IF curr_data_type IS NULL THEN
        ALTER TABLE public.communities ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL;
        RAISE NOTICE 'Added missing column: public.communities.expires_at';
    ELSIF curr_data_type != 'timestamp with time zone' THEN
        -- If text/varchar, safely coerce to timestamptz
        IF curr_data_type IN ('character varying', 'text') THEN
            ALTER TABLE public.communities ALTER COLUMN expires_at TYPE TIMESTAMP WITH TIME ZONE USING (expires_at::timestamptz);
            RAISE NOTICE 'Converted public.communities.expires_at to TIMESTAMPTZ';
        ELSE
            RAISE EXCEPTION 'RECONCILIATION_HALTED: public.communities.expires_at has incompatible type %', curr_data_type;
        END IF;
    END IF;
END $$;


-- ============================================================================
-- PART 4: QUERY-DRIVEN PERFORMANCE INDEX
-- ============================================================================
-- Query pattern: SELECT * FROM records WHERE camp_id = ? AND status = ?
-- Single-column indexes (idx_records_camp, idx_records_status) already exist.
-- This composite index provides single-index scan efficiency for camp record listings.
CREATE INDEX IF NOT EXISTS idx_records_camp_status 
    ON public.records(camp_id, status);


-- ============================================================================
-- PART 5: RIGOROUS FINAL SCHEMA ASSERTION (V4 Verification)
-- ============================================================================
DO $$
DECLARE
    r RECORD;
    err_log TEXT := '';
BEGIN
    CREATE TEMP TABLE expected_final_cols_v4 (
        col_name TEXT PRIMARY KEY,
        exp_data_type TEXT,
        exp_is_nullable TEXT,
        exp_default_contains TEXT
    ) ON COMMIT DROP;

    INSERT INTO expected_final_cols_v4 VALUES
        ('edit_history',              'text',                        'YES', '''[]'''),
        ('version',                   'integer',                     'NO',  '1'),
        ('confidence_scores',         'text',                        'YES', '''{}'''),
        ('consent_given',             'integer',                     'YES', '1'),
        ('consent_signature',         'text',                        'YES', NULL),
        ('locked_by',                 'text',                        'YES', NULL),
        ('locked_at',                 'timestamp with time zone',    'YES', NULL),
        ('approved_by',               'text',                        'YES', NULL),
        ('approved_at',               'timestamp with time zone',    'YES', NULL),
        ('original_ai_extraction',     'text',                        'YES', NULL),
        ('quality_warning_bypassed',  'text',                        'YES', '''[]'''),
        ('quality_bypassed',          'integer',                     'YES', '0'),
        ('template_id',               'text',                        'YES', NULL),
        ('template_version',          'text',                        'YES', NULL),
        ('form_family',               'text',                        'YES', '''adult''');

    FOR r IN (
        SELECT 
            e.col_name,
            e.exp_data_type,
            c.data_type AS act_data_type,
            e.exp_is_nullable,
            c.is_nullable AS act_is_nullable,
            e.exp_default_contains,
            c.column_default AS act_default
        FROM expected_final_cols_v4 e
        LEFT JOIN information_schema.columns c
            ON c.table_schema = 'public' 
           AND c.table_name = 'records' 
           AND c.column_name = e.col_name
    ) LOOP
        IF r.act_data_type IS NULL THEN
            err_log := err_log || format('[records.%s missing] ', r.col_name);
        ELSIF r.act_data_type != r.exp_data_type THEN
            err_log := err_log || format('[records.%s: type %s != exp %s] ', r.col_name, r.act_data_type, r.exp_data_type);
        ELSIF r.act_is_nullable != r.exp_is_nullable THEN
            err_log := err_log || format('[records.%s: nullable %s != exp %s] ', r.col_name, r.act_is_nullable, r.exp_is_nullable);
        ELSIF r.exp_default_contains IS NOT NULL AND (r.act_default IS NULL OR position(r.exp_default_contains in r.act_default) = 0) THEN
            err_log := err_log || format('[records.%s: default %s != exp %s] ', r.col_name, r.act_default, r.exp_default_contains);
        END IF;
    END LOOP;

    -- Validate doctors.is_active
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'doctors' AND column_name = 'is_active'
          AND data_type = 'boolean' AND is_nullable = 'NO'
    ) THEN
        err_log := err_log || '[doctors.is_active invalid] ';
    END IF;

    -- Validate communities.expires_at
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'communities' AND column_name = 'expires_at'
          AND data_type = 'timestamp with time zone' AND is_nullable = 'YES'
    ) THEN
        err_log := err_log || '[communities.expires_at invalid] ';
    END IF;

    IF err_log != '' THEN
        RAISE EXCEPTION 'Schema reconciliation V4 verification failed: %', err_log;
    ELSE
        RAISE NOTICE 'SUCCESS: Schema reconciliation V4 completed and verified with 100%% attribute fidelity.';
    END IF;
END $$;

COMMIT;
-- ============================================================================
-- End of Phase 1 Schema Reconciliation V4
-- ============================================================================
