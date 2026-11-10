-- 0017: مجموعات الموردين (المرحلة 3ب — شاشة «مجموعات الموردين»، النموذج 1 اختيار المستخدم 2026-11-10).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- الفكرة (mockups/phase3/03_supplier_groups.html — نسخة من مجموعات العملاء 0014):
--   * كل مورد (الشاشة الجاية) بيبقى في مجموعة، والمجموعة بتحدد **حساب الرقابة** اللي المورد بيتقيّد عليه
--     (الافتراضي 2101 الموردين — أو أي حساب فرعي بدور «الموردين» لو 2101 اتفرّع) + **الافتراضي** اللي بيتنسخ
--     للمورد الجديد: مدة السداد، العملة، ض.ق.م، خصم المنبع (اللي **إحنا** بنخصمه من المورد ← 2104).
--   * المستخدم اختار النموذج 1 من غير رد على الأسئلة ← اتعمل بالاقتراحات:
--       (1) مجموعة جاهزة واحدة «موردين — عام» (نظامية) والباقي من الشاشة · (2) كل المجموعات على 2101 ·
--       (3) **مفيش حد ائتمان** للمورد · (4) خصم المنبع الافتراضي على المجموعة · (5) شاشة لوحدها في «المشتريات».
--   * الكود أرقام بيتولّد لوحده (1، 2، 3…) وثابت بعد الإضافة · الاسم مايتكررش.
--   * الحساب المربوط بمجموعة مايتمسحش ولا يتوقف ولا يتحوّل رئيسي — إلا بالتفريع (acc.split_account)
--     وساعتها المجموعة بتروح لـ «… — عام» لوحدها في نفس الـ transaction.
--   * كود الضريبة المربوط بمجموعة مايتمسحش ولا يتوقف ولا يتغيّر نوعه.
-- لسه (مع شاشة الموردين): المجموعة اللي فيها موردين مابتتمسحش (FK RESTRICT من party.suppliers).

CREATE TABLE party.supplier_groups (
    id                  integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                varchar(10)   NOT NULL,
    name_ar             varchar(120)  NOT NULL,
    name_en             varchar(120),
    control_account_id  integer       NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    payment_terms_days  smallint      NOT NULL DEFAULT 30,
    currency_code       char(3)       NOT NULL DEFAULT 'EGP' REFERENCES core.currencies (code) ON DELETE RESTRICT,
    vat_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- ض.ق.م (إضافة)
    wht_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- خصم المنبع (خصم)
    notes               varchar(500),
    is_system           boolean       NOT NULL DEFAULT false,
    is_active           boolean       NOT NULL DEFAULT true,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    row_version         integer       NOT NULL DEFAULT 1 CONSTRAINT supplier_groups_row_version_chk CHECK (row_version > 0),
    CONSTRAINT supplier_groups_code_unique UNIQUE (code),
    CONSTRAINT supplier_groups_code_format CHECK (code ~ '^[0-9]{1,10}$'),
    CONSTRAINT supplier_groups_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT supplier_groups_terms_range CHECK (payment_terms_days BETWEEN 0 AND 365),
    CONSTRAINT supplier_groups_system_active CHECK (is_active OR NOT is_system)
);
CREATE UNIQUE INDEX supplier_groups_name_unique ON party.supplier_groups (lower(btrim(name_ar)));
CREATE INDEX supplier_groups_account_idx ON party.supplier_groups (control_account_id);
CREATE INDEX supplier_groups_currency_idx ON party.supplier_groups (currency_code);
CREATE INDEX supplier_groups_vat_idx ON party.supplier_groups (vat_tax_code_id);
CREATE INDEX supplier_groups_wht_idx ON party.supplier_groups (wht_tax_code_id);
COMMENT ON TABLE party.supplier_groups IS
    'مجموعات الموردين: حساب الرقابة + الافتراضي (مدة السداد، العملة، الضرايب) اللي بيتنسخ للمورد الجديد';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.supplier_groups_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a  acc.accounts%ROWTYPE;
    t  acc.tax_codes%ROWTYPE;
    c  core.currencies%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_system THEN
            RAISE EXCEPTION 'المجموعة «%» نظامية (الافتراضية لأي مورد جديد) — مابتتمسحش.', OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.code <> OLD.code THEN
            RAISE EXCEPTION 'كود المجموعة «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        IF NEW.is_system <> OLD.is_system THEN
            RAISE EXCEPTION '«نظامية» بتتحدد من البرنامج بس.';
        END IF;
        IF OLD.is_system AND NOT NEW.is_active THEN
            RAISE EXCEPTION 'المجموعة «%» نظامية (الافتراضية لأي مورد جديد) — مابتتوقفش.', OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- حساب الرقابة: فرعي نشط من الخصوم بدور «الموردين» (FOR SHARE: لو جهاز تاني بيوقفه في نفس اللحظة يستنى).
    IF TG_OP = 'INSERT' OR NEW.control_account_id IS DISTINCT FROM OLD.control_account_id THEN
        SELECT * INTO a FROM acc.accounts WHERE id = NEW.control_account_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'حساب الرقابة مش موجود في الدليل (ممكن يكون اتمسح).';
        END IF;
        IF NOT a.is_leaf OR NOT a.is_active THEN
            RAISE EXCEPTION 'حساب الرقابة «% %» لازم يبقى فرعي ونشط (بيقبل قيود).', a.code, a.name_ar;
        END IF;
        IF a.account_type <> 'liability' OR a.role_code IS DISTINCT FROM 'suppliers' THEN
            RAISE EXCEPTION 'حساب الرقابة «% %» لازم يبقى من حسابات «الموردين» (2101 أو حساب فرعي منه).',
                a.code, a.name_ar;
        END IF;
    END IF;

    -- العملة: نشطة.
    IF TG_OP = 'INSERT' OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
        SELECT * INTO c FROM core.currencies WHERE code = NEW.currency_code FOR SHARE;
        IF NOT FOUND OR NOT c.is_active THEN
            RAISE EXCEPTION 'العملة «%» مش موجودة أو موقوفة.', NEW.currency_code;
        END IF;
    END IF;

    -- ض.ق.م: كود «إضافة» نشط بيتطبق على المشتريات.
    IF NEW.vat_tax_code_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.vat_tax_code_id IS DISTINCT FROM OLD.vat_tax_code_id) THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.vat_tax_code_id FOR SHARE;
        IF NOT FOUND OR NOT t.is_active OR t.kind <> 'add' OR t.applies_to NOT IN ('purch', 'both') THEN
            RAISE EXCEPTION 'ض.ق.م الافتراضي لازم يبقى كود ضريبة «إضافة» نشط بيتطبق على المشتريات.';
        END IF;
    END IF;

    -- خصم المنبع: كود «خصم» نشط بيتطبق على المشتريات.
    IF NEW.wht_tax_code_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.wht_tax_code_id IS DISTINCT FROM OLD.wht_tax_code_id) THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.wht_tax_code_id FOR SHARE;
        IF NOT FOUND OR NOT t.is_active OR t.kind <> 'deduct' OR t.applies_to NOT IN ('purch', 'both') THEN
            RAISE EXCEPTION 'خصم المنبع الافتراضي لازم يبقى كود ضريبة «خصم» نشط بيتطبق على المشتريات.';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER supplier_groups_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON party.supplier_groups
    FOR EACH ROW EXECUTE FUNCTION party.supplier_groups_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON party.supplier_groups
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- حساب الرقابة المربوط بمجموعة موردين: مايتمسحش ولا يتوقف ولا يتحوّل رئيسي (رسالة عربي بدل خطأ الـ FK).
-- (اسمه accounts_zsgroups عشان يطلع بعد رسايل النظامي والضريبة والافتراضي ومجموعات العملاء.)
-- الاستثناء: التفريع (acc.split_account) — والمجموعة بتروح للعام جوه الدالة.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_supplier_groups_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg(name_ar, '، ' ORDER BY code) INTO linked
    FROM party.supplier_groups WHERE control_account_id = OLD.id;
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» حساب رقابة لمجموعات الموردين (%) — غيّر حساب المجموعة الأول.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf
       AND coalesce(current_setting('acc.split_account_id', true), '') = OLD.id::text THEN
        RETURN NEW;                                   -- التفريع: المجموعة بتتنقل للعام جوه الدالة
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR (OLD.is_leaf AND NOT NEW.is_leaf) THEN
        RAISE EXCEPTION 'الحساب «% %» حساب رقابة لمجموعات الموردين (%) — مينفعش يتوقف ولا يتحوّل رئيسي.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF NEW.role_code IS DISTINCT FROM OLD.role_code OR NEW.account_type <> OLD.account_type THEN
        RAISE EXCEPTION 'الحساب «% %» حساب رقابة لمجموعات الموردين (%) — دوره ونوعه مايتغيّروش.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zsgroups_links_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_supplier_groups_guard();

-- كود الضريبة المربوط بمجموعة موردين: مايتمسحش ولا يتوقف ولا يتغيّر نوعه أو يبطل يتطبق على المشتريات.
CREATE FUNCTION acc.tax_codes_supplier_groups_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg(name_ar, '، ' ORDER BY code) INTO linked
    FROM party.supplier_groups WHERE OLD.id IN (vat_tax_code_id, wht_tax_code_id);
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'كود الضريبة «%» افتراضي لمجموعات الموردين (%) — غيّره في المجموعات الأول.', OLD.code, linked;
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR NEW.kind <> OLD.kind
       OR NEW.applies_to NOT IN ('purch', 'both') THEN
        RAISE EXCEPTION 'كود الضريبة «%» افتراضي لمجموعات الموردين (%) — مينفعش يتوقف ولا يتغيّر نوعه.',
            OLD.code, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tax_codes_zsgroups_links_trg
    BEFORE UPDATE OR DELETE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION acc.tax_codes_supplier_groups_guard();

-- ---------------------------------------------------------------------------
-- التفريع: نفس 0014 بالظبط + مجموعات الموردين بتروح لـ «— عام»
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acc.split_account(p_account_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم الحساب «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    p              acc.accounts%ROWTYPE;
    v_sales        integer[];
    v_purchase     integer[];
    v_general      integer;
    v_code         varchar(20);
    v_suffix       constant text := ' — عام';
BEGIN
    -- 1) القفل: أي جهاز تاني بيعدّل / بيفرّع / بيربط ضريبة على الحساب ده بيستنى لحد ما نخلص.
    SELECT * INTO p FROM acc.accounts WHERE id = p_account_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الحساب ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT p.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» رئيسي أصلاً — ضيف تحته على طول.', p.code, p.name_ar;
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'الحساب «% %» في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.', p.code, p.name_ar;
    END IF;
    IF NOT p.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه الأول وبعدين فرّع منه.', p.code, p.name_ar;
    END IF;

    SELECT array_agg(id) FILTER (WHERE sales_account_id = p.id),
           array_agg(id) FILTER (WHERE purchase_account_id = p.id)
      INTO v_sales, v_purchase
      FROM acc.tax_codes WHERE p.id IN (sales_account_id, purchase_account_id);

    -- 2) فك ربط الضريبة مؤقتاً (بيرجع على «العام» تحت) — من غير استثناء في قاعدة الضريبة.
    UPDATE acc.tax_codes SET sales_account_id = NULL WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = NULL WHERE id = ANY (v_purchase);

    -- 3) الحساب يتحوّل رئيسي (الاستثناء للحساب ده بس، وبيتقفل تاني على طول).
    PERFORM set_config('acc.split_account_id', p.id::text, true);
    UPDATE acc.accounts SET is_leaf = false WHERE id = p.id;
    PERFORM set_config('acc.split_account_id', '', true);

    -- 4) أول حساب تحته «— عام» (مع كل تفريع) بياخد الدور والنظامي والربط وكل اللي بيتورث.
    v_code := coalesce(nullif(btrim(p_general_code), ''),
                       p.code || CASE WHEN p.level <= 1 THEN '1' ELSE '01' END);
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, party_kind, requires_cost_center, currency_code,
                              is_active, is_system)
    VALUES (v_code, left(p.name_ar, 120 - length(v_suffix)) || v_suffix,
            CASE WHEN p.name_en IS NULL THEN NULL ELSE left(p.name_en, 110) || ' — General' END,
            p.id, true, p.account_type, p.nature, p.classification, p.cash_flow, p.role_code, p.party_kind,
            p.requires_cost_center, p.currency_code, true, p.is_system)
    RETURNING id INTO v_general;

    -- 5) TODO المرحلة 4 (القيود) — قرار المستخدم (أ): نقل الحركة القديمة للعام في نفس الـ transaction:
    --    UPDATE acc.journal_lines SET account_id = v_general WHERE account_id = p.id;
    --    (سؤال مفتوح: الأرصدة المجمّعة لو اتعملت جدول لوحدها بتتنقل هنا كمان.)

    -- 6) ربط أكواد الضريبة والحسابات الافتراضية ومجموعات العملاء والموردين يرجعوا على العام.
    UPDATE acc.tax_codes SET sales_account_id = v_general WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = v_general WHERE id = ANY (v_purchase);
    UPDATE acc.default_accounts SET account_id = v_general WHERE account_id = p.id;
    UPDATE party.customer_groups SET control_account_id = v_general WHERE control_account_id = p.id;
    UPDATE party.supplier_groups SET control_account_id = v_general WHERE control_account_id = p.id;
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_account(integer, varchar) IS
    'تفريع حساب فرعي: يتحوّل رئيسي + «<الاسم> — عام» بالدور والنظامي وربط الضريبة والحسابات الافتراضية ومجموعات العملاء والموردين (والقيود — المرحلة 4)';

-- ---------------------------------------------------------------------------
-- المجموعة الجاهزة «موردين — عام» (نظامية): حساب الرقابة = حساب «الموردين» الافتراضي (0012) ولو مش متحدد
-- أول حساب فرعي نشط بدور «الموردين» · ض.ق.م 14% · من غير خصم منبع · 30 يوم · العملة الأساسية.
-- (المجموعات التانية بيضيفها المستخدم من الشاشة.)
-- ---------------------------------------------------------------------------
INSERT INTO party.supplier_groups (code, name_ar, name_en, control_account_id, payment_terms_days,
                                   currency_code, vat_tax_code_id, notes, is_system)
SELECT '1', 'موردين — عام', 'General Suppliers', acc_id, 30,
       (SELECT code FROM core.currencies WHERE is_base LIMIT 1),
       (SELECT id FROM acc.tax_codes WHERE code = 'VAT14' AND is_active AND kind = 'add'
          AND applies_to IN ('purch', 'both')),
       'المجموعة الافتراضية لأي مورد جديد', true
FROM (SELECT coalesce(
        (SELECT d.account_id FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id
          WHERE d.key = 'suppliers' AND a.is_leaf AND a.is_active AND a.role_code = 'suppliers'),
        (SELECT id FROM acc.accounts WHERE role_code = 'suppliers' AND is_leaf AND is_active ORDER BY code LIMIT 1))
      AS acc_id) s
WHERE s.acc_id IS NOT NULL;
