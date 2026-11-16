-- ============================================================================
-- Ink 2 Insight (I2I) — Phase 1: Complete Identity, Access & Clinical Schema
-- ============================================================================
-- Target Architecture: Supabase PostgreSQL 12+ / Cloud Hosted Database
-- 
-- The 4 Core Principles Enforced by this Schema:
-- 1. Authentication : Who is logging in? (users, profiles, auth_sessions, password_resets)
-- 2. Membership     : Which community do they belong to? (communities, community_memberships, invitations)
-- 3. Authorization  : What are they allowed to do? (roles, permissions, role_permissions)
-- 4. Station Access : Which registered device/station can they use? (stations, station_assignments)
-- ============================================================================

-- Enable required cryptographic and UUID extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- PART 1: CORE AUTHORIZATION & RBAC MATRIX (Roles & Permissions)
-- ============================================================================

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,                       -- 'super_admin', 'community_admin', 'doctor', 'field_worker'
    name TEXT NOT NULL,
    description TEXT,
    hierarchy_level INTEGER NOT NULL DEFAULT 0, -- super_admin: 100, community_admin: 50, doctor: 30, field_worker: 10
    is_system BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS permissions (
    id TEXT PRIMARY KEY,                       -- e.g. 'records:approve', 'stations:unlock', 'communities:invite'
    name TEXT NOT NULL,
    category TEXT NOT NULL,                    -- 'identity', 'community', 'camp', 'station', 'clinical', 'billing'
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id TEXT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, permission_id)
);

-- Seed Default Standard Roles
INSERT INTO roles (id, name, description, hierarchy_level) VALUES
    ('super_admin', 'Super Administrator', 'Platform-wide administrator with root privileges across all communities', 100),
    ('community_admin', 'Community Administrator', 'Clinic or outreach lead managing camps, staff invitations, and stations', 50),
    ('doctor', 'Physician / Medical Officer', 'Licensed clinical professional authorized to review and approve patient records', 30),
    ('field_worker', 'Field Intake Worker', 'Camp operator permitted to operate intake scanners and capture demographic forms', 10)
ON CONFLICT (id) DO NOTHING;

-- Seed Granular Permissions
INSERT INTO permissions (id, name, category, description) VALUES
    -- Identity & Community Permissions
    ('community:create', 'Create Community', 'community', 'Ability to register a new clinical workspace'),
    ('community:manage', 'Manage Community', 'community', 'Edit community profile, change invite codes, manage members'),
    ('community:invite', 'Invite Staff', 'community', 'Generate and revoke tokenized staff invitation codes'),
    ('community:view_members', 'View Members', 'community', 'List all doctors, staff, and workers in the community'),
    
    -- Camp Management Permissions
    ('camp:create', 'Create Camp', 'camp', 'Set up and configure a new field medical camp'),
    ('camp:manage', 'Manage Camp', 'camp', 'Update camp dates, targets, and assign staff'),
    ('camp:view', 'View Camps', 'camp', 'List camps and view camp dashboard statistics'),
    
    -- Station & Shared Device Permissions
    ('station:register', 'Register Station', 'station', 'Enroll a physical smartphone/tablet station into a camp'),
    ('station:manage', 'Manage Stations', 'station', 'Assign stations to camps and configure station PINs'),
    ('station:unlock', 'Unlock Station', 'station', 'Enter secure PIN to activate or resume a station session'),
    
    -- Clinical Record Permissions
    ('record:scan', 'Scan / Create Record', 'clinical', 'Upload medical intake forms and initiate AI extraction'),
    ('record:view_identity', 'View Identity Fields', 'clinical', 'View demographic and identity fields of patient records'),
    ('record:edit_identity', 'Edit Identity Fields', 'clinical', 'Correct name, age, gender, and school/institution fields'),
    ('record:view_clinical', 'View Clinical Fields', 'clinical', 'View clinical examination notes, vitals, and diagnoses'),
    ('record:edit_clinical', 'Edit Clinical Fields', 'clinical', 'Modify vitals, flags, and specialist findings'),
    ('record:review', 'Review Record', 'clinical', 'Submit reviewed flags on patient intake forms'),
    ('record:approve', 'Approve Record', 'clinical', 'Sign off and formally authorize a clinical intake record'),
    ('record:delete', 'Delete Record', 'clinical', 'Delete draft or duplicate records'),
    ('record:erase_dpdp', 'DPDP Right to Erasure', 'clinical', 'Permanently purge patient health records under DPDP Act 2023'),
    
    -- Audit & Export Permissions
    ('export:data', 'Export Camp Data', 'clinical', 'Download camp records in CSV, Excel, or PDF format'),
    ('audit:view', 'View Audit Logs', 'security', 'Inspect security, login, and record edit audit trails')
ON CONFLICT (id) DO NOTHING;

-- Map Permissions to Roles
-- 1. Super Admin: ALL permissions
INSERT INTO role_permissions (role_id, permission_id)
SELECT 'super_admin', id FROM permissions
ON CONFLICT DO NOTHING;

-- 2. Community Admin
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('community_admin', 'community:manage'),
    ('community_admin', 'community:invite'),
    ('community_admin', 'community:view_members'),
    ('community_admin', 'camp:create'),
    ('community_admin', 'camp:manage'),
    ('community_admin', 'camp:view'),
    ('community_admin', 'station:register'),
    ('community_admin', 'station:manage'),
    ('community_admin', 'station:unlock'),
    ('community_admin', 'record:scan'),
    ('community_admin', 'record:view_identity'),
    ('community_admin', 'record:edit_identity'),
    ('community_admin', 'record:view_clinical'),
    ('community_admin', 'record:edit_clinical'),
    ('community_admin', 'record:review'),
    ('community_admin', 'record:approve'),
    ('community_admin', 'record:delete'),
    ('community_admin', 'record:erase_dpdp'),
    ('community_admin', 'export:data'),
    ('community_admin', 'audit:view')
ON CONFLICT DO NOTHING;

-- 3. Doctor (Clinical Reviewer)
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('doctor', 'camp:view'),
    ('doctor', 'station:unlock'),
    ('doctor', 'record:scan'),
    ('doctor', 'record:view_identity'),
    ('doctor', 'record:edit_identity'),
    ('doctor', 'record:view_clinical'),
    ('doctor', 'record:edit_clinical'),
    ('doctor', 'record:review'),
    ('doctor', 'record:approve'),
    ('doctor', 'export:data')
ON CONFLICT DO NOTHING;

-- 4. Field Worker (Scanning & Demographic Intake Only)
INSERT INTO role_permissions (role_id, permission_id) VALUES
    ('field_worker', 'camp:view'),
    ('field_worker', 'station:unlock'),
    ('field_worker', 'record:scan'),
    ('field_worker', 'record:view_identity'),
    ('field_worker', 'record:edit_identity')
ON CONFLICT DO NOTHING;


-- ============================================================================
-- PART 2: AUTHENTICATION & USER IDENTITY
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,                       -- Bcrypt password hash ($2b$...)
    password_salt TEXT,
    full_name TEXT NOT NULL,
    phone TEXT,
    avatar_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'suspended', 'pending_verification')) DEFAULT 'active',
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified_at TIMESTAMPTZ,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- Doctor/Staff Clinical Profiles (Supplemental clinical details)
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    specialization TEXT DEFAULT 'General Physician',
    medical_license_number TEXT,
    qualification TEXT,
    bio TEXT,
    default_pin_hash TEXT,                     -- Optional individual quick-PIN hash
    default_pin_salt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Password Recovery Requests
CREATE TABLE IF NOT EXISTS password_resets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,           -- SHA-256 hash of random reset token
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(token_hash);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);


-- ============================================================================
-- PART 3: COMMUNITIES & MULTI-TENANT MEMBERSHIPS
-- ============================================================================

CREATE TABLE IF NOT EXISTS communities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,                 -- Human-readable code e.g. 'CAMP-2026' or 'ROTARY'
    slug TEXT UNIQUE,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    creator_name TEXT NOT NULL,
    creator_email TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_communities_code ON communities(code);

-- Multi-Community Membership (Many-to-Many: User <-> Community with Scoped Role)
CREATE TABLE IF NOT EXISTS community_memberships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    community_id UUID NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES roles(id) DEFAULT 'doctor',
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'revoked')) DEFAULT 'active',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, community_id)
);

CREATE INDEX IF NOT EXISTS idx_memberships_user ON community_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_memberships_community ON community_memberships(community_id);
CREATE INDEX IF NOT EXISTS idx_memberships_role ON community_memberships(role_id);

-- Tokenized Single-Use & Multi-Use Invitations
CREATE TABLE IF NOT EXISTS invitations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code TEXT UNIQUE NOT NULL,                 -- e.g. 'INV-DOC-A1B2C3'
    community_id UUID NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    email TEXT,                                -- Optional: restrict invitation to specific email
    role_id TEXT NOT NULL REFERENCES roles(id) DEFAULT 'doctor',
    max_uses INTEGER NOT NULL DEFAULT 1,       -- 1 for single-use, N for bulk team invites
    use_count INTEGER NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invitations_code ON invitations(code);
CREATE INDEX IF NOT EXISTS idx_invitations_comm ON invitations(community_id);
CREATE INDEX IF NOT EXISTS idx_invitations_expires ON invitations(expires_at);


-- ============================================================================
-- PART 4: CAMPS & FIELD STATIONS (Shared Device Management)
-- ============================================================================

CREATE TABLE IF NOT EXISTS camps (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    community_id UUID NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    date_str TEXT NOT NULL DEFAULT 'Today',
    location TEXT DEFAULT 'Field Camp',
    expected_records INTEGER NOT NULL DEFAULT 100,
    target_group TEXT NOT NULL CHECK (target_group IN ('adult', 'paediatric')) DEFAULT 'adult',
    status TEXT NOT NULL CHECK (status IN ('planned', 'active', 'completed', 'archived')) DEFAULT 'active',
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_camps_community ON camps(community_id);
CREATE INDEX IF NOT EXISTS idx_camps_status ON camps(status);

-- Staff Assignments to Camps
CREATE TABLE IF NOT EXISTS camp_staff_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    camp_id UUID NOT NULL REFERENCES camps(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_in_camp TEXT NOT NULL DEFAULT 'doctor',
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (camp_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_camp_staff_camp ON camp_staff_assignments(camp_id);
CREATE INDEX IF NOT EXISTS idx_camp_staff_user ON camp_staff_assignments(user_id);

-- Physical Scanning Stations (Shared Phones/Tablets)
CREATE TABLE IF NOT EXISTS stations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    community_id UUID NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    camp_id UUID REFERENCES camps(id) ON DELETE SET NULL,
    station_name TEXT NOT NULL,                -- e.g. 'Station 01 - General Vitals'
    station_code TEXT UNIQUE NOT NULL,         -- e.g. 'STN-01'
    device_identifier TEXT,                    -- Unique device hash / MAC / browser UUID
    pin_hash TEXT NOT NULL,                    -- Bcrypt hashed 4-6 digit station unlock PIN
    pin_salt TEXT NOT NULL,
    is_locked BOOLEAN NOT NULL DEFAULT FALSE,
    locked_at TIMESTAMPTZ,
    locked_by UUID REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'maintenance', 'decommissioned')) DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stations_comm ON stations(community_id);
CREATE INDEX IF NOT EXISTS idx_stations_camp ON stations(camp_id);
CREATE INDEX IF NOT EXISTS idx_stations_code ON stations(station_code);


-- ============================================================================
-- PART 5: SESSIONS & SECURITY AUDIT LOGS
-- ============================================================================

-- Active User & Station Sessions
CREATE TABLE IF NOT EXISTS auth_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id UUID REFERENCES stations(id) ON DELETE SET NULL,
    community_id UUID REFERENCES communities(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,                  -- SHA-256 hash of issued JWT/access token
    remember_me BOOLEAN NOT NULL DEFAULT FALSE,
    device_info TEXT,                          -- User-Agent / Mobile OS details
    ip_address TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);

-- Comprehensive Security & Authentication Audit Log
CREATE TABLE IF NOT EXISTS auth_audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    community_id UUID REFERENCES communities(id) ON DELETE SET NULL,
    station_id UUID REFERENCES stations(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,                  -- 'login_success', 'login_failed', 'pin_unlock', 'station_lock', 'logout', 'password_reset', 'invite_redeemed', 'lockout'
    ip_address TEXT,
    user_agent TEXT,
    status TEXT NOT NULL DEFAULT 'success',    -- 'success', 'failure', 'blocked'
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auth_audit_user ON auth_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_comm ON auth_audit_log(community_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_event ON auth_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_auth_audit_created ON auth_audit_log(created_at);

-- Brute Force Account / IP Lockout Table
CREATE TABLE IF NOT EXISTS auth_lockouts (
    account_key TEXT PRIMARY KEY,              -- Email, Phone or IP address
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One-Time Passwords (OTP) Table
CREATE TABLE IF NOT EXISTS auth_otps (
    recipient TEXT PRIMARY KEY,
    otp_code TEXT DEFAULT 'HASHED',
    otp_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auth_otps_expires ON auth_otps(expires_at);


-- ============================================================================
-- PART 6: PRESERVED CLINICAL RECORDS & WORKFLOW STORAGE
-- ============================================================================

-- 1. Adult Clinical Intake Records
CREATE TABLE IF NOT EXISTS adult_records (
    id TEXT PRIMARY KEY,
    camp_id UUID REFERENCES camps(id) ON DELETE CASCADE,
    community_id UUID REFERENCES communities(id) ON DELETE CASCADE,
    person_name TEXT NOT NULL,
    age TEXT,
    gender TEXT,
    height TEXT,
    weight TEXT,
    blood_pressure TEXT,
    pulse TEXT,
    rbs TEXT,
    waist_circumference TEXT,
    ecg_notes TEXT,
    echo_notes TEXT,
    examination_details TEXT,
    referral_details TEXT,
    marital_status TEXT,
    parent_spouse_name TEXT,
    no_of_children TEXT,
    address TEXT,
    mobile_number TEXT,
    blood_group TEXT,
    dob TEXT,
    admission_id TEXT,
    moc_doc_id TEXT,
    school_college_name TEXT,
    staff_student_type TEXT,
    shift TEXT,
    year_of_study TEXT,
    course_dept TEXT,
    section TEXT,
    medical_history TEXT DEFAULT '{}',
    family_history TEXT DEFAULT '{}',
    vision_category TEXT,
    distant_vision_right TEXT,
    distant_vision_left TEXT,
    dental_status TEXT,
    dental_abnormality TEXT,
    dental_advice TEXT,
    consent_given INTEGER DEFAULT 1,
    consent_signature TEXT,
    status TEXT NOT NULL CHECK(status IN ('needs_review', 'reviewed', 'approved', 'failed')) DEFAULT 'needs_review',
    missing_fields TEXT NOT NULL DEFAULT '[]',
    uncertain_fields TEXT NOT NULL DEFAULT '[]',
    extraction_notes TEXT,
    structured_record TEXT,
    page1_preview TEXT,
    page2_preview TEXT,
    reviewer_notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    scanned_by TEXT,
    edit_history TEXT DEFAULT '[]',
    version INTEGER DEFAULT 1,
    confidence_scores TEXT DEFAULT '{}',
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    original_ai_extraction TEXT,
    quality_warning_bypassed TEXT DEFAULT '[]',
    quality_bypassed INTEGER DEFAULT 0,
    template_id TEXT,
    template_version TEXT,
    form_family TEXT DEFAULT 'adult',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_adult_records_camp ON adult_records(camp_id);
CREATE INDEX IF NOT EXISTS idx_adult_records_status ON adult_records(status);
CREATE INDEX IF NOT EXISTS idx_adult_records_community ON adult_records(community_id);

-- 2. Paediatric Screening Records
CREATE TABLE IF NOT EXISTS paediatric_records (
    id TEXT PRIMARY KEY,
    camp_id UUID REFERENCES camps(id) ON DELETE CASCADE,
    community_id UUID REFERENCES communities(id) ON DELETE CASCADE,
    person_name TEXT NOT NULL,
    age TEXT,
    gender TEXT,
    height TEXT,
    weight TEXT,
    bmi TEXT,
    bmi_centile REAL,
    bmi_classification TEXT,
    blood_pressure TEXT,
    bp_percentile REAL,
    bp_classification TEXT,
    pulse TEXT,
    spinal_caliper_atr REAL,
    head_circumference TEXT,
    muac TEXT,
    screening_type TEXT,
    school_name TEXT,
    class_and_section TEXT,
    admission_id TEXT,
    moc_doc_id TEXT,
    dob TEXT,
    blood_group TEXT,
    parent_guardian_name TEXT,
    emergency_contact_phone TEXT,
    address TEXT,
    birth_history TEXT,
    milestones TEXT,
    immunization_status TEXT,
    dietary_habits TEXT,
    known_allergies TEXT,
    vital_signs_status TEXT,
    gait_status TEXT,
    ears_hearing_status TEXT,
    otoscopy_findings TEXT,
    oral_cavity_throat TEXT,
    lymph_nodes TEXT,
    systemic_findings TEXT DEFAULT '{}',
    vision_distant_right TEXT,
    vision_distant_left TEXT,
    color_vision TEXT,
    squint_strabismus TEXT,
    dental_caries TEXT,
    malocclusion TEXT,
    oral_hygiene TEXT,
    examination_details TEXT,
    referral_specialist TEXT,
    referral_reason TEXT,
    referral_details TEXT,
    paediatric_alerts TEXT DEFAULT '[]',
    consent_given INTEGER DEFAULT 1,
    consent_signature TEXT,
    status TEXT NOT NULL CHECK(status IN ('needs_review', 'reviewed', 'approved', 'failed')) DEFAULT 'needs_review',
    missing_fields TEXT NOT NULL DEFAULT '[]',
    uncertain_fields TEXT NOT NULL DEFAULT '[]',
    extraction_notes TEXT,
    structured_record TEXT,
    page1_preview TEXT,
    page2_preview TEXT,
    reviewer_notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    scanned_by TEXT,
    edit_history TEXT DEFAULT '[]',
    version INTEGER DEFAULT 1,
    confidence_scores TEXT DEFAULT '{}',
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    original_ai_extraction TEXT,
    quality_warning_bypassed TEXT DEFAULT '[]',
    quality_bypassed INTEGER DEFAULT 0,
    template_id TEXT,
    template_version TEXT,
    form_family TEXT DEFAULT 'paediatric',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paediatric_records_camp ON paediatric_records(camp_id);
CREATE INDEX IF NOT EXISTS idx_paediatric_records_status ON paediatric_records(status);
CREATE INDEX IF NOT EXISTS idx_paediatric_records_community ON paediatric_records(community_id);

-- 3. Legacy Unified Records Table
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    camp_id UUID REFERENCES camps(id) ON DELETE CASCADE,
    community_id UUID REFERENCES communities(id) ON DELETE CASCADE,
    person_name TEXT NOT NULL,
    age TEXT,
    gender TEXT,
    height TEXT,
    weight TEXT,
    blood_pressure TEXT,
    examination_details TEXT,
    referral_details TEXT,
    missing_fields TEXT NOT NULL DEFAULT '[]',
    uncertain_fields TEXT NOT NULL DEFAULT '[]',
    extraction_notes TEXT,
    structured_record TEXT,
    status TEXT NOT NULL CHECK(status IN ('needs_review', 'reviewed', 'approved', 'failed')),
    page1_preview TEXT,
    page2_preview TEXT,
    reviewer_notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    scanned_by TEXT,
    target_group TEXT NOT NULL DEFAULT 'adult',
    is_paediatric INTEGER DEFAULT 0,
    paediatric_alerts TEXT DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_records_camp ON records(camp_id);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_community ON records(community_id);

-- 4. Clinical Record Audit Log (Field-level changes)
CREATE TABLE IF NOT EXISTS record_audit_log (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_role TEXT NOT NULL,
    action TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_record ON record_audit_log(record_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON record_audit_log(org_id);

-- 5. Organizations (Billing Quota & Metering)
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT DEFAULT 'outreach_10k',
    papers_included INTEGER NOT NULL DEFAULT 10000,
    addon_papers INTEGER NOT NULL DEFAULT 0,
    papers_used INTEGER NOT NULL DEFAULT 0,
    renews_on TIMESTAMPTZ,
    rollover_papers INTEGER NOT NULL DEFAULT 0,
    ai_paused INTEGER NOT NULL DEFAULT 0,
    daily_cap INTEGER NOT NULL DEFAULT 2000
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id SERIAL PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    record_id TEXT,
    action TEXT NOT NULL,
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_think INTEGER DEFAULT 0,
    seconds REAL DEFAULT 0.0,
    cost_rs REAL DEFAULT 0.0,
    provider TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    razorpay_payment_id TEXT UNIQUE,
    papers INTEGER NOT NULL,
    amount_rs REAL NOT NULL,
    gst_rs REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    number TEXT UNIQUE NOT NULL,
    period TEXT NOT NULL,
    amount_rs REAL NOT NULL,
    gst_rs REAL NOT NULL,
    status TEXT NOT NULL,
    pdf_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scan_hashes (
    hash TEXT NOT NULL,
    org_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, hash),
    FOREIGN KEY (org_id) REFERENCES orgs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    record_id TEXT,
    file_type TEXT NOT NULL,
    original_filename TEXT,
    storage_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- PART 7: BACKWARD COMPATIBILITY VIEWS (Seamless Legacy Support)
-- ============================================================================

-- Legacy 'doctors' view: ensures existing backend queries querying 'doctors'
-- continue to resolve seamlessly while pulling from the normalized users table.
CREATE OR REPLACE VIEW doctors AS
SELECT 
    u.id::text AS id,
    u.id::text AS auth_id,
    u.email,
    u.full_name,
    COALESCE(up.specialization, 'General Physician') AS specialization,
    u.avatar_url,
    u.phone,
    cm.community_id::text AS community_id,
    c.code AS community_code,
    COALESCE(cm.role_id, 'doctor') AS community_role,
    s.pin_hash,
    s.pin_salt,
    u.created_at,
    u.updated_at
FROM users u
LEFT JOIN user_profiles up ON up.user_id = u.id
LEFT JOIN community_memberships cm ON cm.user_id = u.id
LEFT JOIN communities c ON c.id = cm.community_id
LEFT JOIN stations s ON s.community_id = cm.community_id;

-- Legacy 'invites' view mapping to the normalized 'invitations' table
CREATE OR REPLACE VIEW invites AS
SELECT 
    id::text AS id,
    code,
    community_id::text AS community_id,
    email,
    role_id AS role,
    expires_at,
    CASE WHEN is_revoked THEN 1 ELSE 0 END AS is_revoked,
    created_by::text AS created_by,
    created_at
FROM invitations;

