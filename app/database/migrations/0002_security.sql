-- 0002: نظام الأمان والصلاحيات (منقول من Hr GAD — docs/02 القسم 3) في schema sec
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قواعد الصلاحيات (زي Hr GAD):
--   role_permissions.allowed : true = المجموعة واخدة الصلاحية (مفيش صف = مش واخداها)
--   user_permissions.allowed : true = سماح للمستخدم، false = منع (مفيش صف = يورث من المجموعة)
--   المدير / الصلاحية الكاملة = كل حاجة مسموحة.
-- المدير الافتراضي بيتعمل من البرنامج وقت الفتح (كلمة السر محتاجة PBKDF2 من بايثون).

-- ---------------------------------------------------------------------------
-- مجموعات الصلاحيات
-- ---------------------------------------------------------------------------
CREATE TABLE sec.roles (
    id            serial PRIMARY KEY,
    role_code     varchar(50)  NOT NULL,
    role_name_ar  varchar(120) NOT NULL,
    role_name_en  varchar(120),
    description   text,
    is_active     boolean      NOT NULL DEFAULT true,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT roles_code_not_blank CHECK (btrim(role_code) <> ''),
    CONSTRAINT roles_name_not_blank CHECK (btrim(role_name_ar) <> '')
);
CREATE UNIQUE INDEX roles_code_uq ON sec.roles (lower(role_code));
COMMENT ON TABLE sec.roles IS 'مجموعات الصلاحيات';

-- ---------------------------------------------------------------------------
-- المستخدمين
-- ---------------------------------------------------------------------------
CREATE TABLE sec.app_users (
    id             serial PRIMARY KEY,
    username       varchar(60)  NOT NULL,
    password_hash  varchar(255) NOT NULL,           -- PBKDF2-SHA256 (عمرها ما بتتخزن نص صريح)
    full_name      varchar(120),
    job_title      varchar(120),                     -- المهنة (بتظهر في الهيدر)
    email          varchar(120),
    phone          varchar(40),
    role_id        integer REFERENCES sec.roles(id) ON DELETE SET NULL,
    language       varchar(10)  NOT NULL DEFAULT 'ar',
    is_active      boolean      NOT NULL DEFAULT true,
    is_admin       boolean      NOT NULL DEFAULT false,
    full_access    boolean      NOT NULL DEFAULT false,
    notes          text,
    photo          bytea,
    photo_mime     varchar(50),
    last_login_at  timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT app_users_username_not_blank CHECK (btrim(username) <> ''),
    CONSTRAINT app_users_language_chk CHECK (language IN ('ar', 'en'))
);
-- اسم المستخدم مايتكررش من غير حساسية لحروف كبيرة/صغيرة.
CREATE UNIQUE INDEX app_users_username_uq ON sec.app_users (lower(username));
CREATE INDEX app_users_role_idx ON sec.app_users (role_id);
COMMENT ON TABLE sec.app_users IS 'مستخدمين البرنامج';

-- حماية في القاعدة نفسها: مايتقفلش ولا يتلغي آخر مدير فعّال.
CREATE FUNCTION sec.guard_last_admin() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM sec.app_users)
       AND NOT EXISTS (SELECT 1 FROM sec.app_users
                       WHERE is_active AND (is_admin OR full_access)) THEN
        RAISE EXCEPTION 'لا يمكن إيقاف أو إلغاء آخر مستخدم مدير فعّال في النظام.';
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER app_users_guard_last_admin
    AFTER UPDATE OR DELETE ON sec.app_users
    FOR EACH STATEMENT EXECUTE FUNCTION sec.guard_last_admin();

-- ---------------------------------------------------------------------------
-- الصلاحيات المتاحة (بتتولّد من permission_registry وقت الفتح — عمرها ما بتتمسح)
-- ---------------------------------------------------------------------------
CREATE TABLE sec.permissions (
    id              serial PRIMARY KEY,
    permission_code varchar(150) NOT NULL UNIQUE,   -- module.target.action
    permission_type varchar(20)  NOT NULL,
    module_code     varchar(50)  NOT NULL,
    module_name_ar  varchar(120),
    module_name_en  varchar(120),
    category_ar     varchar(120),
    category_en     varchar(120),
    target_code     varchar(80)  NOT NULL,
    target_name_ar  varchar(150),
    target_name_en  varchar(150),
    action_code     varchar(40)  NOT NULL,
    action_name_ar  varchar(80),
    action_name_en  varchar(80),
    sort_order      integer      NOT NULL DEFAULT 0, -- ترتيب المصفوفة زي القائمة
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT permissions_type_chk CHECK (permission_type IN ('screen', 'report')),
    CONSTRAINT permissions_code_chk CHECK (permission_code = module_code || '.' || target_code || '.' || action_code)
);
CREATE INDEX permissions_active_idx ON sec.permissions (is_active);
COMMENT ON TABLE sec.permissions IS 'كل الصلاحيات المتاحة (module.target.action)';

-- ---------------------------------------------------------------------------
-- صلاحيات المجموعة + تخصيص المستخدم (+ كلمة سر الإجراء)
-- ---------------------------------------------------------------------------
CREATE TABLE sec.role_permissions (
    id                   serial PRIMARY KEY,
    role_id              integer NOT NULL REFERENCES sec.roles(id) ON DELETE CASCADE,
    permission_id        integer NOT NULL REFERENCES sec.permissions(id) ON DELETE CASCADE,
    allowed              boolean NOT NULL DEFAULT false,
    action_password_hash text,                       -- كلمة سر الإجراء (حذف الكل / إظهار الإجماليات)
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (role_id, permission_id),
    CONSTRAINT role_permissions_password_needs_allow CHECK (action_password_hash IS NULL OR allowed)
);
CREATE INDEX role_permissions_role_idx ON sec.role_permissions (role_id);
COMMENT ON TABLE sec.role_permissions IS 'صلاحيات مجموعة الصلاحيات';

CREATE TABLE sec.user_permissions (
    id                   serial PRIMARY KEY,
    user_id              integer NOT NULL REFERENCES sec.app_users(id) ON DELETE CASCADE,
    permission_id        integer NOT NULL REFERENCES sec.permissions(id) ON DELETE CASCADE,
    allowed              boolean NOT NULL,           -- true = سماح، false = منع
    action_password_hash text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, permission_id),
    CONSTRAINT user_permissions_password_needs_allow CHECK (action_password_hash IS NULL OR allowed)
);
CREATE INDEX user_permissions_user_idx ON sec.user_permissions (user_id);
COMMENT ON TABLE sec.user_permissions IS 'تخصيص صلاحيات المستخدم فوق مجموعته (سماح / منع)';

-- ---------------------------------------------------------------------------
-- سجل الدخول والخروج
-- ---------------------------------------------------------------------------
CREATE TABLE sec.user_login_logs (
    id          bigserial PRIMARY KEY,
    user_id     integer REFERENCES sec.app_users(id) ON DELETE SET NULL,
    username    varchar(60),
    login_time  timestamptz NOT NULL DEFAULT now(),
    logout_time timestamptz,
    status      varchar(20) NOT NULL,
    ip_address  varchar(60),
    device_name varchar(120),
    notes       text,                                -- سبب الفشل
    CONSTRAINT user_login_logs_status_chk CHECK (status IN ('success', 'failed'))
);
CREATE INDEX user_login_logs_user_idx ON sec.user_login_logs (user_id, login_time DESC);
CREATE INDEX user_login_logs_time_idx ON sec.user_login_logs (login_time DESC);
COMMENT ON TABLE sec.user_login_logs IS 'سجل الدخول والخروج (الناجح والفاشل بالسبب)';

-- ---------------------------------------------------------------------------
-- سجل التعديلات (قرار المستخدم: قبل/بعد بسطور المستند، في نفس الـ transaction)
-- details = {"v":1, "before":{...}|null, "after":{...}|null,
--            "lines":{"<جدول السطور>":{"before":[...], "after":[...]}}}
-- ---------------------------------------------------------------------------
CREATE TABLE sec.audit_log (
    id            bigserial PRIMARY KEY,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    user_id       integer REFERENCES sec.app_users(id) ON DELETE SET NULL,
    username      varchar(60),
    action        varchar(20) NOT NULL,
    entity        varchar(80) NOT NULL,              -- اسم الجدول (مثلاً sec.app_users)
    entity_label  varchar(120),                      -- بالعربي (مستخدم، قيد يومية…)
    entity_id     varchar(60),
    record_label  varchar(200),                      -- اسم السجل أو رقم المستند
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    device_name   varchar(120),
    CONSTRAINT audit_log_action_chk CHECK (action IN (
        'create', 'update', 'delete', 'activate', 'deactivate', 'password',
        'permissions', 'post', 'unpost', 'approve', 'reverse'))
);
CREATE INDEX audit_log_entity_idx ON sec.audit_log (entity, entity_id, occurred_at DESC);
CREATE INDEX audit_log_time_idx ON sec.audit_log (occurred_at DESC);
CREATE INDEX audit_log_user_idx ON sec.audit_log (user_id, occurred_at DESC);
COMMENT ON TABLE sec.audit_log IS 'سجل التعديلات: مين عمل إيه وإمتى، وقبل/بعد بسطور المستند';

-- السجل للإضافة بس: ممنوع تعديله أو مسحه.
CREATE FUNCTION sec.audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'سجل التعديلات للقراءة بس — ممنوع تعديله أو مسحه.';
END $$;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON sec.audit_log
    FOR EACH ROW EXECUTE FUNCTION sec.audit_log_append_only();
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON sec.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION sec.audit_log_append_only();
