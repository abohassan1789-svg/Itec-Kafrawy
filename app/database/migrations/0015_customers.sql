-- 0015: العملاء (المرحلة 3أ — شاشة «العملاء»، النموذج 1 اختيار المستخدم 2026-11-10).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/02_customers.html + ردود المستخدم 2026-11-10 + القرار 2 في docs/03):
--   * **حساب رقابة + بُعد الطرف:** مفيش حساب لكل عميل في الدليل — حساب الرقابة بييجي من **المجموعة**
--     (party.customer_groups.control_account_id)، والعميل بيبقى بُعد على سطر القيد (المرحلة 4).
--   * **الكود** أرقام بيتولّد لوحده **من 1** (ترقيم جديد — مش أرقام الأكسس) وثابت بعد الإضافة.
--   * **الاسم** مطلوب ومايتكررش.
--   * **رقم التسجيل الضريبي** اختياري، **مايتكررش**، ومن غير عدد أرقام معيّن — بيقبل أرقام وحروف.
--     التكرار بيتحسب من غير مسافات وشُرَط ونقط وسلاش ومن غير فرق كابيتال/سمول (100-200-301 = 100200301).
--   * **حد الائتمان تنبيه بس** (مع الفاتورة — مش منع) · 0 = من غير حد.
--   * **المندوب** على الفاتورة بس (اختياري) — مش على العميل.
--   * مدة السداد وحد الائتمان والعملة وض.ق.م وخصم المنبع بيتنسخوا من المجموعة للعميل الجديد، ويتعدّلوا.
--   * المجموعة الموقوفة مابتتختارش لعميل جديد (ولا لنقل عميل ليها) · المجموعة اللي فيها عملاء مابتتمسحش.
-- لسه: رصيد أول المدة = سطر في مستند الأرصدة الافتتاحية (المرحلة 4) · العميل اللي عليه فاتورة / سند / أمر
-- توريد / قيد مايتمسحش (FK RESTRICT من الجداول دي لما تتعمل).

CREATE TABLE party.customers (
    id                  integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                varchar(10)   NOT NULL,
    name_ar             varchar(200)  NOT NULL,
    name_en             varchar(200),
    group_id            integer       NOT NULL REFERENCES party.customer_groups (id) ON DELETE RESTRICT,
    tax_reg_no          varchar(30),                                   -- رقم التسجيل الضريبي (اختياري)
    contact_person      varchar(120),
    mobile              varchar(30),
    phone               varchar(30),
    email               varchar(120),
    address             varchar(300),
    payment_terms_days  smallint      NOT NULL DEFAULT 30,
    credit_limit        numeric(18,2) NOT NULL DEFAULT 0,             -- 0 = من غير حد (تنبيه بس)
    currency_code       char(3)       NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    vat_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- ض.ق.م (إضافة)
    wht_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- خصم المنبع (خصم)
    notes               varchar(500),
    is_active           boolean       NOT NULL DEFAULT true,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    row_version         integer       NOT NULL DEFAULT 1 CONSTRAINT customers_row_version_chk CHECK (row_version > 0),
    CONSTRAINT customers_code_unique UNIQUE (code),
    CONSTRAINT customers_code_format CHECK (code ~ '^[0-9]{1,10}$'),
    CONSTRAINT customers_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT customers_tax_not_blank CHECK (tax_reg_no IS NULL OR btrim(tax_reg_no) <> ''),
    CONSTRAINT customers_terms_range CHECK (payment_terms_days BETWEEN 0 AND 365),
    CONSTRAINT customers_limit_chk CHECK (credit_limit >= 0)
);

-- الرقم الضريبي «منظّف» للتكرار والبحث: من غير مسافات وشُرَط ونقط وسلاش وبحروف كابيتال.
CREATE FUNCTION party.normalize_tax_no(p text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT nullif(upper(regexp_replace(coalesce(p, ''), '[[:space:]./-]', '', 'g')), '')
$$;

CREATE UNIQUE INDEX customers_name_unique ON party.customers (lower(btrim(name_ar)));
CREATE UNIQUE INDEX customers_tax_unique ON party.customers (party.normalize_tax_no(tax_reg_no))
    WHERE tax_reg_no IS NOT NULL;
CREATE INDEX customers_group_idx ON party.customers (group_id);
CREATE INDEX customers_currency_idx ON party.customers (currency_code);
CREATE INDEX customers_vat_idx ON party.customers (vat_tax_code_id);
CREATE INDEX customers_wht_idx ON party.customers (wht_tax_code_id);
CREATE INDEX customers_mobile_idx ON party.customers (mobile);
CREATE INDEX customers_active_idx ON party.customers (is_active, group_id);
COMMENT ON TABLE party.customers IS
    'العملاء: بُعد على سطر القيد — حساب الرقابة من المجموعة · الكود من 1 · الاسم والرقم الضريبي مايتكرروش';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل)
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.customers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    g  party.customer_groups%ROWTYPE;
    t  acc.tax_codes%ROWTYPE;
    c  core.currencies%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.code <> OLD.code THEN
            RAISE EXCEPTION 'كود العميل «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- المجموعة: نشطة (FOR SHARE: لو جهاز تاني بيوقفها في نفس اللحظة يستنى).
    IF TG_OP = 'INSERT' OR NEW.group_id IS DISTINCT FROM OLD.group_id THEN
        SELECT * INTO g FROM party.customer_groups WHERE id = NEW.group_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'مجموعة العملاء مش موجودة (ممكن تكون اتمسحت).';
        END IF;
        IF NOT g.is_active THEN
            RAISE EXCEPTION 'المجموعة «%» موقوفة — مينفعش تختارها لعميل.', g.name_ar;
        END IF;
    END IF;

    -- العملة: نشطة.
    IF TG_OP = 'INSERT' OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
        SELECT * INTO c FROM core.currencies WHERE code = NEW.currency_code FOR SHARE;
        IF NOT FOUND OR NOT c.is_active THEN
            RAISE EXCEPTION 'العملة «%» مش موجودة أو موقوفة.', NEW.currency_code;
        END IF;
    END IF;

    -- ض.ق.م: كود «إضافة» نشط بيتطبق على المبيعات.
    IF NEW.vat_tax_code_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.vat_tax_code_id IS DISTINCT FROM OLD.vat_tax_code_id) THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.vat_tax_code_id FOR SHARE;
        IF NOT FOUND OR NOT t.is_active OR t.kind <> 'add' OR t.applies_to NOT IN ('sales', 'both') THEN
            RAISE EXCEPTION 'ض.ق.م للعميل لازم يبقى كود ضريبة «إضافة» نشط بيتطبق على المبيعات.';
        END IF;
    END IF;

    -- خصم المنبع: كود «خصم» نشط بيتطبق على المبيعات.
    IF NEW.wht_tax_code_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.wht_tax_code_id IS DISTINCT FROM OLD.wht_tax_code_id) THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.wht_tax_code_id FOR SHARE;
        IF NOT FOUND OR NOT t.is_active OR t.kind <> 'deduct' OR t.applies_to NOT IN ('sales', 'both') THEN
            RAISE EXCEPTION 'خصم المنبع للعميل لازم يبقى كود ضريبة «خصم» نشط بيتطبق على المبيعات.';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER customers_guard_trg
    BEFORE INSERT OR UPDATE ON party.customers
    FOR EACH ROW EXECUTE FUNCTION party.customers_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON party.customers
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- المجموعة اللي فيها عملاء: مابتتمسحش (رسالة عربي بدل خطأ الـ FK).
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.customer_groups_customers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM party.customers WHERE group_id = OLD.id;
    IF n > 0 THEN
        RAISE EXCEPTION 'المجموعة «%» فيها % عميل — انقلهم لمجموعة تانية أو وقّف المجموعة بدل ما تمسحها.',
            OLD.name_ar, n;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER customer_groups_zcustomers_trg
    BEFORE DELETE ON party.customer_groups
    FOR EACH ROW EXECUTE FUNCTION party.customer_groups_customers_guard();

-- ---------------------------------------------------------------------------
-- كود الضريبة المربوط بعميل: مايتمسحش ولا يتوقف ولا يتغيّر نوعه أو ناحيته.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.tax_codes_customers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM party.customers WHERE OLD.id IN (vat_tax_code_id, wht_tax_code_id);
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'كود الضريبة «%» متسجل على % عميل — غيّره عندهم الأول.', OLD.code, n;
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR NEW.kind <> OLD.kind
       OR NEW.applies_to NOT IN ('sales', 'both') THEN
        RAISE EXCEPTION 'كود الضريبة «%» متسجل على % عميل — مينفعش يتوقف ولا يتغيّر نوعه.', OLD.code, n;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tax_codes_zcustomers_links_trg
    BEFORE UPDATE OR DELETE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION acc.tax_codes_customers_guard();
