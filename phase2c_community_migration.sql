-- ============================================================================
-- I2I CLINICAL INTELLIGENCE PLATFORM
-- PHASE 2C: COMMUNITY MEMBERSHIP & INVITATION TRANSACTION FUNCTIONS
-- ============================================================================
-- Status: PROPOSED MIGRATION — NOT EXECUTED
-- DO NOT EXECUTE WITHOUT EXPLICIT APPROVAL
--
-- Compatible with: PostgreSQL 13+ / Supabase PostgreSQL
--
-- SECURITY:
--   * SECURITY DEFINER functions use a fixed search_path.
--   * Function execution is revoked from PUBLIC, anon and authenticated.
--   * Only service_role receives EXECUTE permission.
--   * Flask must verify the Supabase JWT before calling these functions.
--   * Flask must derive user ID, email, email verification and account type
--     from trusted authentication data, not request-body values.
--   * public.users is checked for matching user ID, email and active status.
--   * Email verification and account_type remain trusted Flask assertions.
--
-- SCHEMA ASSUMPTIONS VERIFIED IN THE LIVE DATABASE:
--   * community_memberships already has the required status constraint.
--   * community_memberships already has approval/rejection audit columns.
--   * invitations already has requires_admin_approval.
--   * invitation_responses already exists with its required constraints.
--   * Existing database role field_worker represents the application User.
--
-- THIS IS A FUNCTIONS-ONLY MIGRATION:
--   It intentionally does not alter tables, constraints or indexes.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. REVOKE PRIVILEGES FROM ALL EXISTING FUNCTIONS WITH THESE NAMES
-- ----------------------------------------------------------------------------
-- This includes obsolete overloads. Known obsolete signatures are dropped
-- below without CASCADE, so dependencies cause a safe failure instead of
-- silently deleting dependent objects.
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT p.oid::regprocedure AS func_signature
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n
          ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prokind = 'f'
          AND p.proname IN (
              'rpc_redeem_invitation_atomic',
              'rpc_accept_invitation_atomic',
              'rpc_decline_invitation_atomic',
              'rpc_create_community_atomic',
              'rpc_admin_review_membership_atomic',
              'rpc_approve_membership_atomic'
          )
    LOOP
        EXECUTE pg_catalog.format(
            'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role',
            r.func_signature
        );
    END LOOP;
END;
$$;

-- ----------------------------------------------------------------------------
-- 2. DROP KNOWN OBSOLETE FUNCTION OVERLOADS
-- ----------------------------------------------------------------------------
-- Current canonical signatures are retained and replaced below.
-- No CASCADE is used.
-- ----------------------------------------------------------------------------

DROP FUNCTION IF EXISTS public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
);

DROP FUNCTION IF EXISTS public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT, TEXT, JSONB
);

DROP FUNCTION IF EXISTS public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
);

DROP FUNCTION IF EXISTS public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT, TEXT, JSONB
);

DROP FUNCTION IF EXISTS public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT, JSONB
);

DROP FUNCTION IF EXISTS public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, JSONB
);

DROP FUNCTION IF EXISTS public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, JSONB
);

DROP FUNCTION IF EXISTS public.rpc_approve_membership_atomic(
    UUID, UUID, BOOLEAN, TEXT
);

DROP FUNCTION IF EXISTS public.rpc_admin_review_membership_atomic(
    UUID, UUID, BOOLEAN, TEXT
);


-- ============================================================================
-- 3. READ-ONLY INVITATION PREVIEW
-- ============================================================================
-- Function:
--   rpc_redeem_invitation_atomic(
--       p_code,
--       p_user_id,
--       p_user_email,
--       p_email_verified,
--       p_account_type
--   )
--
-- This function ONLY validates and previews an invitation.
-- It does not increment use_count, insert a response, or create membership.
-- The acceptance function below performs those writes atomically.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.rpc_redeem_invitation_atomic(
    p_code TEXT,
    p_user_id UUID,
    p_user_email TEXT,
    p_email_verified BOOLEAN,
    p_account_type TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_user RECORD;
    v_inv RECORD;
    v_comm RECORD;
    v_existing_resp RECORD;
    v_existing_mem RECORD;
    v_target_role RECORD;

    v_clean_code TEXT;
    v_clean_email TEXT;
    v_clean_account_type TEXT;
BEGIN
    -- 1. Validate required inputs.

    IF p_code IS NULL OR pg_catalog.btrim(p_code) = '' THEN
        RAISE EXCEPTION
            'INVALID_CODE: Invitation code is required and cannot be empty.';
    END IF;

    IF p_user_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_USER: User ID is required.';
    END IF;

    IF p_user_email IS NULL OR pg_catalog.btrim(p_user_email) = '' THEN
        RAISE EXCEPTION
            'INVALID_EMAIL: User email is required and cannot be empty.';
    END IF;

    IF p_account_type IS NULL OR pg_catalog.btrim(p_account_type) = '' THEN
        RAISE EXCEPTION
            'INVALID_ACCOUNT_TYPE: Trusted account type is required.';
    END IF;

    v_clean_account_type := pg_catalog.lower(
        pg_catalog.btrim(p_account_type)
    );

    IF v_clean_account_type NOT IN ('doctor', 'user') THEN
        RAISE EXCEPTION
            'INVALID_ACCOUNT_TYPE: Account type must be doctor or user.';
    END IF;

    IF p_email_verified IS NOT TRUE THEN
        RAISE EXCEPTION
            'EMAIL_NOT_VERIFIED: Account email must be verified.';
    END IF;

    v_clean_code := pg_catalog.upper(pg_catalog.btrim(p_code));
    v_clean_email := pg_catalog.lower(pg_catalog.btrim(p_user_email));

    -- 2. Confirm the supplied identity matches an active application user.
    -- Email verification itself must be established by Flask from Supabase.

    SELECT id, email, status
    INTO v_user
    FROM public.users
    WHERE id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'USER_NOT_FOUND: No application user exists for this user ID.';
    END IF;

    IF v_user.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'USER_INACTIVE: The application user account is not active.';
    END IF;

    IF pg_catalog.lower(pg_catalog.btrim(v_user.email))
       IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'USER_EMAIL_MISMATCH: Supplied email does not match the application user.';
    END IF;

    -- 3. Find invitation.

    SELECT *
    INTO v_inv
    FROM public.invitations
    WHERE code = v_clean_code;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'INVITATION_NOT_FOUND: Invitation code does not exist.';
    END IF;

    -- 4. Verify associated community exists and is active.

    SELECT id, name, is_active
    INTO v_comm
    FROM public.communities
    WHERE id = v_inv.community_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'COMMUNITY_NOT_FOUND: The invitation community does not exist.';
    END IF;

    IF v_comm.is_active IS NOT TRUE THEN
        RAISE EXCEPTION
            'COMMUNITY_INACTIVE: This community is inactive or archived.';
    END IF;

    -- 5. Validate invitation status and expiry.

    IF v_inv.is_revoked IS NOT FALSE THEN
        RAISE EXCEPTION
            'INVITATION_REVOKED: This invitation has been revoked or is invalid.';
    END IF;

    IF v_inv.expires_at IS NULL OR v_inv.expires_at <= pg_catalog.now() THEN
        RAISE EXCEPTION
            'INVITATION_EXPIRED: This invitation has expired or has an invalid expiry.';
    END IF;

    -- 6. Validate invitation capacity.

    IF v_inv.max_uses IS NULL OR v_inv.use_count IS NULL THEN
        RAISE EXCEPTION
            'INVITATION_INVALID_CAPACITY: Usage counters cannot be NULL.';
    END IF;

    IF v_inv.max_uses <= 0 THEN
        RAISE EXCEPTION
            'INVITATION_INVALID_CAPACITY: max_uses must be greater than zero.';
    END IF;

    IF v_inv.use_count < 0 THEN
        RAISE EXCEPTION
            'INVITATION_INVALID_CAPACITY: use_count cannot be negative.';
    END IF;

    IF v_inv.use_count >= v_inv.max_uses THEN
        RAISE EXCEPTION
            'INVITATION_EXHAUSTED: This invitation has reached its maximum uses (% of %).',
            v_inv.use_count,
            v_inv.max_uses;
    END IF;

    -- 7. Validate optional email targeting.

    IF v_inv.email IS NOT NULL
       AND pg_catalog.btrim(v_inv.email) <> ''
       AND pg_catalog.lower(pg_catalog.btrim(v_inv.email))
           IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'INVITATION_EMAIL_MISMATCH: This invitation was issued for another email.';
    END IF;

    -- 8. Standard invitations cannot grant administrative roles.

    IF v_inv.role_id IN ('community_admin', 'super_admin') THEN
        RAISE EXCEPTION
            'PRIVILEGED_ROLE_PROHIBITED: Administrative roles cannot be granted through standard invitation redemption.';
    END IF;

    -- 9. Enforce account type to community role compatibility.
    -- The database role field_worker represents the application User account.

    IF v_clean_account_type = 'doctor'
       AND v_inv.role_id IS DISTINCT FROM 'doctor' THEN
        RAISE EXCEPTION
            'ROLE_INCOMPATIBLE: Doctor accounts may only receive the doctor community role.';
    END IF;

    IF v_clean_account_type = 'user'
       AND v_inv.role_id IS DISTINCT FROM 'field_worker' THEN
        RAISE EXCEPTION
            'ROLE_INCOMPATIBLE: User accounts may only receive the field_worker database role.';
    END IF;

    -- 10. Confirm the invitation role exists.

    SELECT id
    INTO v_target_role
    FROM public.roles
    WHERE id = v_inv.role_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'INVALID_ROLE: The invitation role does not exist.';
    END IF;

    -- 11. Enforce invitation response finality.

    SELECT id, response
    INTO v_existing_resp
    FROM public.invitation_responses
    WHERE invitation_id = v_inv.id
      AND user_id = p_user_id;

    IF FOUND THEN
        IF v_existing_resp.response = 'accepted' THEN
            RAISE EXCEPTION
                'ALREADY_REDEEMED: You have already accepted this invitation.';
        ELSIF v_existing_resp.response = 'declined' THEN
            RAISE EXCEPTION
                'INVITATION_PREVIOUSLY_DECLINED: You have already declined this invitation. A new invitation is required.';
        END IF;
    END IF;

    -- 12. Prevent automatic reactivation or duplicate membership.

    SELECT id, status
    INTO v_existing_mem
    FROM public.community_memberships
    WHERE community_id = v_inv.community_id
      AND user_id = p_user_id;

    IF FOUND THEN
        IF v_existing_mem.status = 'active' THEN
            RAISE EXCEPTION
                'ALREADY_ACTIVE_MEMBER: You are already an active community member.';
        ELSIF v_existing_mem.status = 'pending' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_PENDING_APPROVAL: Your membership is already awaiting approval.';
        ELSIF v_existing_mem.status = 'revoked' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_REVOKED: A revoked membership cannot be reactivated through invitation redemption.';
        ELSIF v_existing_mem.status = 'rejected' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_PREVIOUSLY_REJECTED: A newly authorized invitation is required.';
        ELSIF v_existing_mem.status = 'inactive' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_INACTIVE: An inactive membership cannot be automatically reactivated.';
        ELSE
            RAISE EXCEPTION
                'MEMBERSHIP_EXISTS: A membership already exists in state "%".',
                v_existing_mem.status;
        END IF;
    END IF;

    -- 13. Return preview only. No database writes occur in this function.

    RETURN pg_catalog.jsonb_build_object(
        'valid', TRUE,
        'invitation_id', v_inv.id,
        'community_id', v_inv.community_id,
        'community_name', v_comm.name,
        'role_id', v_inv.role_id,
        'requires_admin_approval', v_inv.requires_admin_approval,
        'will_require_approval', v_inv.requires_admin_approval,
        'expires_at', v_inv.expires_at,
        'max_uses', v_inv.max_uses,
        'use_count', v_inv.use_count,
        'message', 'Invitation code is valid.'
    );
END;
$$;

REVOKE ALL ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_redeem_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;


-- ============================================================================
-- 4. ATOMIC INVITATION ACCEPTANCE
-- ============================================================================
-- Performs all acceptance writes in one database transaction:
--   1. Lock invitation.
--   2. Validate user, invitation and community.
--   3. Serialize same-user/same-community acceptances.
--   4. Increment invitation use_count.
--   5. Insert accepted response.
--   6. Insert membership as active or pending.
--
-- If any step fails, PostgreSQL rolls back the entire function transaction.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.rpc_accept_invitation_atomic(
    p_code TEXT,
    p_user_id UUID,
    p_user_email TEXT,
    p_email_verified BOOLEAN,
    p_account_type TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_user RECORD;
    v_inv RECORD;
    v_comm RECORD;
    v_existing_resp RECORD;
    v_existing_mem RECORD;
    v_target_role RECORD;

    v_status TEXT;
    v_clean_code TEXT;
    v_clean_email TEXT;
    v_clean_account_type TEXT;
    v_rows_updated INTEGER;
BEGIN
    -- 1. Validate required inputs.

    IF p_code IS NULL OR pg_catalog.btrim(p_code) = '' THEN
        RAISE EXCEPTION
            'INVALID_CODE: Invitation code is required.';
    END IF;

    IF p_user_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_USER: User ID is required.';
    END IF;

    IF p_user_email IS NULL OR pg_catalog.btrim(p_user_email) = '' THEN
        RAISE EXCEPTION
            'INVALID_EMAIL: User email is required.';
    END IF;

    IF p_account_type IS NULL OR pg_catalog.btrim(p_account_type) = '' THEN
        RAISE EXCEPTION
            'INVALID_ACCOUNT_TYPE: Trusted account type is required.';
    END IF;

    v_clean_account_type := pg_catalog.lower(
        pg_catalog.btrim(p_account_type)
    );

    IF v_clean_account_type NOT IN ('doctor', 'user') THEN
        RAISE EXCEPTION
            'INVALID_ACCOUNT_TYPE: Account type must be doctor or user.';
    END IF;

    IF p_email_verified IS NOT TRUE THEN
        RAISE EXCEPTION
            'EMAIL_NOT_VERIFIED: Account email must be verified.';
    END IF;

    v_clean_code := pg_catalog.upper(pg_catalog.btrim(p_code));
    v_clean_email := pg_catalog.lower(pg_catalog.btrim(p_user_email));

    -- 2. Validate application user identity.

    SELECT id, email, status
    INTO v_user
    FROM public.users
    WHERE id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'USER_NOT_FOUND: No application user exists for this user ID.';
    END IF;

    IF v_user.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'USER_INACTIVE: The application user account is not active.';
    END IF;

    IF pg_catalog.lower(pg_catalog.btrim(v_user.email))
       IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'USER_EMAIL_MISMATCH: Supplied email does not match the application user.';
    END IF;

    -- 3. Lock invitation to serialize acceptance and decline for this code.

    SELECT *
    INTO v_inv
    FROM public.invitations
    WHERE code = v_clean_code
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'INVITATION_NOT_FOUND: Invitation code does not exist.';
    END IF;

    -- 4. Verify community is active.

    SELECT id, name, is_active
    INTO v_comm
    FROM public.communities
    WHERE id = v_inv.community_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'COMMUNITY_NOT_FOUND: The invitation community does not exist.';
    END IF;

    IF v_comm.is_active IS NOT TRUE THEN
        RAISE EXCEPTION
            'COMMUNITY_INACTIVE: This community is inactive or archived.';
    END IF;

    -- 5. Serialize acceptance of different codes by the same user
    --    for the same community.
    --
    -- The unique constraint on (user_id, community_id) remains the final
    -- database-level duplicate membership safeguard.

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtext(p_user_id::TEXT),
        pg_catalog.hashtext(v_inv.community_id::TEXT)
    );

    -- 6. Revalidate invitation state while its row is locked.

    IF v_inv.is_revoked IS NOT FALSE THEN
        RAISE EXCEPTION
            'INVITATION_REVOKED: This invitation has been revoked or is invalid.';
    END IF;

    IF v_inv.expires_at IS NULL OR v_inv.expires_at <= pg_catalog.now() THEN
        RAISE EXCEPTION
            'INVITATION_EXPIRED: This invitation has expired or has an invalid expiry.';
    END IF;

    IF v_inv.max_uses IS NULL OR v_inv.use_count IS NULL THEN
        RAISE EXCEPTION
            'INVITATION_INVALID_CAPACITY: Usage counters cannot be NULL.';
    END IF;

    IF v_inv.max_uses <= 0 OR v_inv.use_count < 0 THEN
        RAISE EXCEPTION
            'INVITATION_INVALID_CAPACITY: Invitation usage counters are invalid.';
    END IF;

    IF v_inv.use_count >= v_inv.max_uses THEN
        RAISE EXCEPTION
            'INVITATION_EXHAUSTED: This invitation has reached its maximum uses (% of %).',
            v_inv.use_count,
            v_inv.max_uses;
    END IF;

    -- 7. Validate optional email targeting.

    IF v_inv.email IS NOT NULL
       AND pg_catalog.btrim(v_inv.email) <> ''
       AND pg_catalog.lower(pg_catalog.btrim(v_inv.email))
           IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'INVITATION_EMAIL_MISMATCH: This invitation was issued for another email.';
    END IF;

    -- 8. Prevent administrative role assignment through invitations.

    IF v_inv.role_id IN ('community_admin', 'super_admin') THEN
        RAISE EXCEPTION
            'PRIVILEGED_ROLE_PROHIBITED: Administrative roles cannot be granted through standard invitation redemption.';
    END IF;

    -- 9. Validate account type and invitation role.

    IF v_clean_account_type = 'doctor'
       AND v_inv.role_id IS DISTINCT FROM 'doctor' THEN
        RAISE EXCEPTION
            'ROLE_INCOMPATIBLE: Doctor accounts may only receive the doctor community role.';
    END IF;

    IF v_clean_account_type = 'user'
       AND v_inv.role_id IS DISTINCT FROM 'field_worker' THEN
        RAISE EXCEPTION
            'ROLE_INCOMPATIBLE: User accounts may only receive the field_worker database role.';
    END IF;

    SELECT id
    INTO v_target_role
    FROM public.roles
    WHERE id = v_inv.role_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'INVALID_ROLE: The invitation role does not exist.';
    END IF;

    -- 10. Check for a previous response to this invitation.

    SELECT id, response
    INTO v_existing_resp
    FROM public.invitation_responses
    WHERE invitation_id = v_inv.id
      AND user_id = p_user_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing_resp.response = 'accepted' THEN
            RAISE EXCEPTION
                'ALREADY_REDEEMED: You have already accepted this invitation.';
        ELSIF v_existing_resp.response = 'declined' THEN
            RAISE EXCEPTION
                'INVITATION_PREVIOUSLY_DECLINED: You have already declined this invitation. A new invitation is required.';
        END IF;
    END IF;

    -- 11. Prevent duplicate or automatic membership reactivation.

    SELECT id, status
    INTO v_existing_mem
    FROM public.community_memberships
    WHERE community_id = v_inv.community_id
      AND user_id = p_user_id;

    IF FOUND THEN
        IF v_existing_mem.status = 'active' THEN
            RAISE EXCEPTION
                'ALREADY_ACTIVE_MEMBER: You are already an active community member.';
        ELSIF v_existing_mem.status = 'pending' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_PENDING_APPROVAL: Your membership is already awaiting approval.';
        ELSIF v_existing_mem.status = 'revoked' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_REVOKED: A revoked membership cannot be reactivated through invitation redemption.';
        ELSIF v_existing_mem.status = 'rejected' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_PREVIOUSLY_REJECTED: A newly authorized invitation is required.';
        ELSIF v_existing_mem.status = 'inactive' THEN
            RAISE EXCEPTION
                'MEMBERSHIP_INACTIVE: An inactive membership cannot be automatically reactivated.';
        ELSE
            RAISE EXCEPTION
                'MEMBERSHIP_EXISTS: A membership already exists in state "%".',
                v_existing_mem.status;
        END IF;
    END IF;

    -- 12. Determine membership state.
    -- Admin-created invitations activate immediately.
    -- Doctor-member-created invitations require Admin approval.

    IF v_inv.requires_admin_approval IS TRUE THEN
        v_status := 'pending';
    ELSE
        v_status := 'active';
    END IF;

    -- 13. Increment usage with a guarded update.

    UPDATE public.invitations
    SET use_count = use_count + 1
    WHERE id = v_inv.id
      AND is_revoked IS FALSE
      AND expires_at > pg_catalog.now()
      AND use_count = v_inv.use_count
      AND use_count < max_uses;

    GET DIAGNOSTICS v_rows_updated = ROW_COUNT;

    IF v_rows_updated <> 1 THEN
        RAISE EXCEPTION
            'INVITATION_USAGE_UPDATE_FAILED: Invitation capacity changed or the invitation is no longer valid.';
    END IF;

    -- 14. Record accepted response.

    INSERT INTO public.invitation_responses (
        invitation_id,
        user_id,
        community_id,
        response,
        responded_at
    )
    VALUES (
        v_inv.id,
        p_user_id,
        v_inv.community_id,
        'accepted',
        pg_catalog.now()
    );

    -- 15. Create membership.

    INSERT INTO public.community_memberships (
        user_id,
        community_id,
        role_id,
        status,
        joined_at,
        updated_at
    )
    VALUES (
        p_user_id,
        v_inv.community_id,
        v_inv.role_id,
        v_status,
        pg_catalog.now(),
        pg_catalog.now()
    );

    -- 16. Return result.

    RETURN pg_catalog.jsonb_build_object(
        'success', TRUE,
        'status', v_status,
        'community_id', v_inv.community_id,
        'community_name', v_comm.name,
        'role_id', v_inv.role_id,
        'message',
        CASE
            WHEN v_status = 'active'
                THEN 'Successfully joined community workspace.'
            ELSE
                'Invitation accepted. Membership is pending Community Admin approval.'
        END
    );
END;
$$;

REVOKE ALL ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_accept_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN, TEXT
) TO service_role;


-- ============================================================================
-- 5. ATOMIC INVITATION DECLINE
-- ============================================================================
-- Records a persistent declined response.
-- Does not increment invitation use_count.
-- A declined invitation cannot subsequently be accepted by the same user.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.rpc_decline_invitation_atomic(
    p_code TEXT,
    p_user_id UUID,
    p_user_email TEXT,
    p_email_verified BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_user RECORD;
    v_inv RECORD;
    v_comm RECORD;
    v_existing_resp RECORD;

    v_clean_code TEXT;
    v_clean_email TEXT;
BEGIN
    -- 1. Validate inputs.

    IF p_code IS NULL OR pg_catalog.btrim(p_code) = '' THEN
        RAISE EXCEPTION
            'INVALID_CODE: Invitation code is required.';
    END IF;

    IF p_user_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_USER: User ID is required.';
    END IF;

    IF p_user_email IS NULL OR pg_catalog.btrim(p_user_email) = '' THEN
        RAISE EXCEPTION
            'INVALID_EMAIL: User email is required.';
    END IF;

    IF p_email_verified IS NOT TRUE THEN
        RAISE EXCEPTION
            'EMAIL_NOT_VERIFIED: Account email must be verified.';
    END IF;

    v_clean_code := pg_catalog.upper(pg_catalog.btrim(p_code));
    v_clean_email := pg_catalog.lower(pg_catalog.btrim(p_user_email));

    -- 2. Validate application user identity.

    SELECT id, email, status
    INTO v_user
    FROM public.users
    WHERE id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'USER_NOT_FOUND: No application user exists for this user ID.';
    END IF;

    IF v_user.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'USER_INACTIVE: The application user account is not active.';
    END IF;

    IF pg_catalog.lower(pg_catalog.btrim(v_user.email))
       IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'USER_EMAIL_MISMATCH: Supplied email does not match the application user.';
    END IF;

    -- 3. Lock invitation to serialize against acceptance on the same code.

    SELECT *
    INTO v_inv
    FROM public.invitations
    WHERE code = v_clean_code
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'INVITATION_NOT_FOUND: Invitation code does not exist.';
    END IF;

    -- 4. Verify associated community.

    SELECT id, is_active
    INTO v_comm
    FROM public.communities
    WHERE id = v_inv.community_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'COMMUNITY_NOT_FOUND: The invitation community does not exist.';
    END IF;

    IF v_comm.is_active IS NOT TRUE THEN
        RAISE EXCEPTION
            'COMMUNITY_INACTIVE: This community is inactive or archived.';
    END IF;

    -- 5. Validate invitation state.

    IF v_inv.is_revoked IS NOT FALSE THEN
        RAISE EXCEPTION
            'INVITATION_REVOKED: This invitation has been revoked or is invalid.';
    END IF;

    IF v_inv.expires_at IS NULL OR v_inv.expires_at <= pg_catalog.now() THEN
        RAISE EXCEPTION
            'INVITATION_EXPIRED: This invitation has expired or has an invalid expiry.';
    END IF;

    -- 6. Validate optional email targeting.

    IF v_inv.email IS NOT NULL
       AND pg_catalog.btrim(v_inv.email) <> ''
       AND pg_catalog.lower(pg_catalog.btrim(v_inv.email))
           IS DISTINCT FROM v_clean_email THEN
        RAISE EXCEPTION
            'INVITATION_EMAIL_MISMATCH: This invitation was issued for another email.';
    END IF;

    -- 7. Enforce response finality.

    SELECT id, response
    INTO v_existing_resp
    FROM public.invitation_responses
    WHERE invitation_id = v_inv.id
      AND user_id = p_user_id
    FOR UPDATE;

    IF FOUND THEN
        IF v_existing_resp.response = 'accepted' THEN
            RAISE EXCEPTION
                'ALREADY_ACCEPTED: You have already accepted this invitation and cannot decline it.';
        ELSIF v_existing_resp.response = 'declined' THEN
            RAISE EXCEPTION
                'ALREADY_DECLINED: You have already declined this invitation.';
        END IF;
    END IF;

    -- 8. Record decline. No invitation usage increment occurs.

    INSERT INTO public.invitation_responses (
        invitation_id,
        user_id,
        community_id,
        response,
        responded_at
    )
    VALUES (
        v_inv.id,
        p_user_id,
        v_inv.community_id,
        'declined',
        pg_catalog.now()
    );

    RETURN pg_catalog.jsonb_build_object(
        'success', TRUE,
        'message', 'Invitation declined.'
    );
END;
$$;

REVOKE ALL ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_decline_invitation_atomic(
    TEXT, UUID, TEXT, BOOLEAN
) TO service_role;


-- ============================================================================
-- 6. ATOMIC COMMUNITY CREATION
-- ============================================================================
-- Creates a community and the founder's active community_admin membership.
--
-- The creator must:
--   * Exist in public.users.
--   * Have active account status.
--   * Have a matching email and full name.
--   * Be a verified Doctor according to trusted Flask authentication data.
--
-- The function uses the canonical name and email stored in public.users.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.rpc_create_community_atomic(
    p_name TEXT,
    p_code TEXT,
    p_creator_id UUID,
    p_creator_name TEXT,
    p_creator_email TEXT,
    p_user_role TEXT,
    p_email_verified BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_creator RECORD;

    v_comm_id UUID;
    v_mem_id UUID;

    v_clean_name TEXT;
    v_clean_code TEXT;
    v_clean_creator_name TEXT;
    v_clean_creator_email TEXT;

    v_constraint_name TEXT;
    v_err_message TEXT;
BEGIN
    -- 1. Validate inputs.

    IF p_creator_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_CREATOR: Creator user ID is required.';
    END IF;

    IF p_creator_name IS NULL OR pg_catalog.btrim(p_creator_name) = '' THEN
        RAISE EXCEPTION
            'CREATOR_NAME_REQUIRED: Creator name is required.';
    END IF;

    IF p_creator_email IS NULL OR pg_catalog.btrim(p_creator_email) = '' THEN
        RAISE EXCEPTION
            'INVALID_EMAIL: Creator email is required.';
    END IF;

    v_clean_name := pg_catalog.btrim(COALESCE(p_name, ''));
    v_clean_code := pg_catalog.upper(
        pg_catalog.btrim(COALESCE(p_code, ''))
    );

    IF v_clean_name = '' THEN
        RAISE EXCEPTION
            'COMMUNITY_NAME_REQUIRED: Community name cannot be empty.';
    END IF;

    IF v_clean_code = '' THEN
        RAISE EXCEPTION
            'COMMUNITY_CODE_REQUIRED: Community code cannot be empty.';
    END IF;

    v_clean_creator_name := pg_catalog.btrim(p_creator_name);
    v_clean_creator_email := pg_catalog.lower(
        pg_catalog.btrim(p_creator_email)
    );

    -- 2. Load canonical creator information from public.users.

    SELECT id, email, full_name, status
    INTO v_creator
    FROM public.users
    WHERE id = p_creator_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'CREATOR_NOT_FOUND: Creator does not exist in public.users.';
    END IF;

    IF v_creator.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'CREATOR_INACTIVE: Creator account is not active.';
    END IF;

    IF pg_catalog.lower(pg_catalog.btrim(v_creator.email))
       IS DISTINCT FROM v_clean_creator_email THEN
        RAISE EXCEPTION
            'CREATOR_EMAIL_MISMATCH: Supplied creator email does not match the application user.';
    END IF;

    IF pg_catalog.btrim(v_creator.full_name)
       IS DISTINCT FROM v_clean_creator_name THEN
        RAISE EXCEPTION
            'CREATOR_NAME_MISMATCH: Supplied creator name does not match the application user.';
    END IF;

    -- 3. Validate trusted account type and email verification.
    -- Flask must derive these values from the verified Supabase identity.

    IF p_user_role IS NULL
       OR pg_catalog.lower(pg_catalog.btrim(p_user_role))
          IS DISTINCT FROM 'doctor' THEN
        RAISE EXCEPTION
            'FORBIDDEN: Only Doctor accounts may create communities.';
    END IF;

    IF p_email_verified IS NOT TRUE THEN
        RAISE EXCEPTION
            'EMAIL_NOT_VERIFIED: Doctor email must be verified to create a community.';
    END IF;

    -- 4. Prepare IDs.

    v_comm_id := pg_catalog.gen_random_uuid();
    v_mem_id := pg_catalog.gen_random_uuid();

    -- 5. Create community.
    -- Unique code conflicts are translated into a stable application error.

    BEGIN
        INSERT INTO public.communities (
            id,
            name,
            code,
            creator_id,
            creator_name,
            creator_email,
            is_active,
            created_at,
            updated_at
        )
        VALUES (
            v_comm_id,
            v_clean_name,
            v_clean_code,
            p_creator_id,
            v_creator.full_name,
            v_creator.email,
            TRUE,
            pg_catalog.now(),
            pg_catalog.now()
        );

    EXCEPTION
        WHEN unique_violation THEN
            GET STACKED DIAGNOSTICS
                v_constraint_name = CONSTRAINT_NAME,
                v_err_message = MESSAGE_TEXT;

            IF COALESCE(v_constraint_name, v_err_message, '')
               ILIKE '%code%' THEN
                RAISE EXCEPTION
                    'COMMUNITY_CODE_TAKEN: Community code "%" is already in use.',
                    v_clean_code;

            ELSIF COALESCE(v_constraint_name, v_err_message, '')
                  ILIKE '%slug%' THEN
                RAISE EXCEPTION
                    'COMMUNITY_SLUG_TAKEN: Community slug is already in use.';

            ELSE
                RAISE EXCEPTION
                    'COMMUNITY_UNIQUE_VIOLATION: Unique constraint violation on "%".',
                    COALESCE(v_constraint_name, v_err_message, 'unknown');
            END IF;
    END;

    -- 6. Create founder membership as Community Admin.

    INSERT INTO public.community_memberships (
        id,
        user_id,
        community_id,
        role_id,
        status,
        joined_at,
        updated_at
    )
    VALUES (
        v_mem_id,
        p_creator_id,
        v_comm_id,
        'community_admin',
        'active',
        pg_catalog.now(),
        pg_catalog.now()
    );

    RETURN pg_catalog.jsonb_build_object(
        'success', TRUE,
        'community_id', v_comm_id,
        'name', v_clean_name,
        'code', v_clean_code,
        'role', 'community_admin'
    );
END;
$$;

REVOKE ALL ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_create_community_atomic(
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, BOOLEAN
) TO service_role;


-- ============================================================================
-- 7. ATOMIC COMMUNITY ADMIN MEMBERSHIP REVIEW
-- ============================================================================
-- Allows an active Community Admin to approve or reject a pending membership.
--
-- Authorizes only active community_admin members of the target community.
-- super_admin authority is deferred until global versus community-scoped
-- permissions have been explicitly finalized.
--
-- Validation & Locking approach:
--   1. Validate inputs.
--   2. Verify administrator exists in public.users and is active.
--   3. Read target membership metadata.
--   4. Reject self-review.
--   5. Lock administrator membership and verify community_admin role.
--   6. Lock and revalidate target membership under concurrency.
--   7. Verify target user exists in public.users and is active.
--   8. Update target membership and maintain consistent audit fields.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.rpc_admin_review_membership_atomic(
    p_membership_id UUID,
    p_admin_user_id UUID,
    p_decision TEXT,
    p_reason TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_admin_user RECORD;
    v_target_user RECORD;
    v_mem RECORD;

    v_admin_role TEXT;
    v_new_status TEXT;
    v_clean_decision TEXT;

    v_target_comm_id UUID;
    v_target_user_id UUID;
    v_target_status TEXT;

    v_rows_updated INTEGER;
BEGIN
    -- 1. Validate inputs.

    IF p_membership_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_MEMBERSHIP: Membership ID is required.';
    END IF;

    IF p_admin_user_id IS NULL THEN
        RAISE EXCEPTION
            'INVALID_ADMIN: Administrator user ID is required.';
    END IF;

    IF p_decision IS NULL OR pg_catalog.btrim(p_decision) = '' THEN
        RAISE EXCEPTION
            'INVALID_DECISION: Decision must be approved or rejected.';
    END IF;

    v_clean_decision := pg_catalog.lower(
        pg_catalog.btrim(p_decision)
    );

    IF v_clean_decision NOT IN ('approved', 'rejected') THEN
        RAISE EXCEPTION
            'INVALID_DECISION: Decision must be approved or rejected.';
    END IF;

    -- 2. Verify administrator exists in public.users and is active.

    SELECT id, status
    INTO v_admin_user
    FROM public.users
    WHERE id = p_admin_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'ADMIN_NOT_FOUND: Administrator user does not exist in public.users.';
    END IF;

    IF v_admin_user.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'ADMIN_INACTIVE: Administrator user account is not active.';
    END IF;

    -- 3. Read target metadata without locking.

    SELECT community_id, user_id, status
    INTO v_target_comm_id, v_target_user_id, v_target_status
    FROM public.community_memberships
    WHERE id = p_membership_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'MEMBERSHIP_NOT_FOUND: Membership record does not exist.';
    END IF;

    IF v_target_status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION
            'MEMBERSHIP_NOT_PENDING: Only pending memberships can be reviewed. Current status is "%".',
            v_target_status;
    END IF;

    -- 4. Prevent self-review.

    IF v_target_user_id = p_admin_user_id THEN
        RAISE EXCEPTION
            'SELF_REVIEW_PROHIBITED: Administrators cannot review their own membership application.';
    END IF;

    -- 5. Lock and verify administrator authority.
    -- Temporarily authorizes only community_admin. super_admin authority
    -- is deferred until global versus community-scoped permissions are finalized.

    SELECT role_id
    INTO v_admin_role
    FROM public.community_memberships
    WHERE user_id = p_admin_user_id
      AND community_id = v_target_comm_id
      AND status = 'active'
    FOR SHARE;

    IF NOT FOUND
       OR v_admin_role IS NULL
       OR v_admin_role IS DISTINCT FROM 'community_admin' THEN
        RAISE EXCEPTION
            'FORBIDDEN: Caller is not an authorized active Community Admin for this community.';
    END IF;

    -- 6. Lock target membership.

    SELECT *
    INTO v_mem
    FROM public.community_memberships
    WHERE id = p_membership_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'MEMBERSHIP_NOT_FOUND: Target membership was deleted or is unavailable.';
    END IF;

    -- 7. Revalidate target identity and state after acquiring the lock.

    IF v_mem.community_id IS DISTINCT FROM v_target_comm_id THEN
        RAISE EXCEPTION
            'MEMBERSHIP_COMMUNITY_CHANGED: Target community changed concurrently.';
    END IF;

    IF v_mem.user_id IS DISTINCT FROM v_target_user_id THEN
        RAISE EXCEPTION
            'MEMBERSHIP_USER_CHANGED: Target user changed concurrently.';
    END IF;

    IF v_mem.status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION
            'MEMBERSHIP_NOT_PENDING: Target membership is no longer pending. Current status is "%".',
            v_mem.status;
    END IF;

    -- 8. Verify target user exists in public.users and is active before applying decision.

    SELECT id, status
    INTO v_target_user
    FROM public.users
    WHERE id = v_mem.user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'TARGET_USER_NOT_FOUND: Target user does not exist in public.users.';
    END IF;

    IF v_target_user.status IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION
            'TARGET_USER_INACTIVE: Target user account is not active.';
    END IF;

    -- 9. Apply decision and maintain mutually consistent audit fields.

    IF v_clean_decision = 'approved' THEN

        v_new_status := 'active';

        UPDATE public.community_memberships
        SET status = 'active',
            approved_by = p_admin_user_id,
            approved_at = pg_catalog.now(),
            rejected_by = NULL,
            rejected_at = NULL,
            rejection_reason = NULL,
            updated_at = pg_catalog.now()
        WHERE id = p_membership_id;

    ELSE

        v_new_status := 'rejected';

        UPDATE public.community_memberships
        SET status = 'rejected',
            rejected_by = p_admin_user_id,
            rejected_at = pg_catalog.now(),
            rejection_reason = NULLIF(pg_catalog.btrim(p_reason), ''),
            approved_by = NULL,
            approved_at = NULL,
            updated_at = pg_catalog.now()
        WHERE id = p_membership_id;

    END IF;

    -- 10. Exactly one target row must have been updated.

    GET DIAGNOSTICS v_rows_updated = ROW_COUNT;

    IF v_rows_updated <> 1 THEN
        RAISE EXCEPTION
            'UPDATE_FAILED: Expected to update exactly one membership record; updated %.',
            v_rows_updated;
    END IF;

    RETURN pg_catalog.jsonb_build_object(
        'success', TRUE,
        'membership_id', p_membership_id,
        'status', v_new_status,
        'community_id', v_mem.community_id,
        'decision', v_clean_decision
    );
END;
$$;

REVOKE ALL ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.rpc_admin_review_membership_atomic(
    UUID, UUID, TEXT, TEXT
) TO service_role;


-- ============================================================================
-- 8. COMPLETE
-- ============================================================================
-- This transaction contains function definitions and privilege changes only.
-- It does not alter tables, constraints, indexes or existing records.
--
-- Before any execution:
--   * Review the final live function catalog and privileges.
--   * Verify all function bodies and overloads.
--   * Confirm Flask routes call these exact signatures.
--   * Confirm Flask verifies JWT identity and trusted claims.
--   * Test acceptance, decline, duplicate requests and concurrency.
--   * Confirm the frontend no longer relies on localStorage for membership.
--
-- Do not execute until explicitly approved.
-- ============================================================================

COMMIT;