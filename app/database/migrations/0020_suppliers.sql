-- 0020: الموردين (المرحلة 3ب — شاشة «الموردين»، النموذج 1 اختيار المستخدم 2026-11-11).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/04_suppliers.html + ردود المستخدم 2026-11-11 + القرار 2 في docs/03):
--   * **حساب رقابة + بُعد الطرف:** مفيش حساب لكل مورد في الدليل — حساب الرقابة بييجي من **المجموعة**
--     (party.supplier_groups.control_account_id — 2101 الموردين)، والمورد بُعد على سطر القيد (المرحلة 4).
--   * **الكود** `SUB-001`, `SUB-002`… بيتولّد لوحده وثابت: المسلسل في `seq` والكود محسوب منه (بأقل حاجة 3 خانات،
--     وبعد 999 بيكمّل SUB-1000 من غير أصفار).
--   * **الاسم** مطلوب ومايتكررش.
--   * **رقم التسجيل الضريبي** اختياري، **مايتكررش**، أرقام وحروف من غير عدد معيّن (نفس العملاء —
--     party.normalize_tax_no من 0015).
--   * **مفيش حد ائتمان** (اتقرر مع المجموعات) · مدة السداد والعملة وض.ق.م وخصم المنبع بيتنسخوا من المجموعة.
--   * **البنك ورقم الحساب / IBAN** اختياريين (للتحويلات).
--   * **«مورد نقدي»** نظامي `SUB-000` للمشتريات الكاش: مابيتمسحش ولا بيتوقف.
--   * ربط المورد بالعميل (النموذج 9) **اتأجّل خالص** لحد ما المستخدم يطلبه.
-- لسه: رصيد أول المدة = سطر في مستند الأرصدة الافتتاحية (المرحلة 4) · المورد اللي عليه فاتورة / سند / شيك / قيد
-- مايتمسحش (FK RESTRICT من الجداول دي لما تتعمل).

CREATE TABLE party.suppliers (
    id                  integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq                 integer       NOT NULL,
    code                varchar(20)   GENERATED ALWAYS AS
                            ('SUB-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar             varchar(200)  NOT NULL,
    name_en             varchar(200),
    group_id            integer       NOT NULL REFERENCES party.supplier_groups (id) ON DELETE RESTRICT,
    tax_reg_no          varchar(30),                                   -- رقم التسجيل الضريبي (اختياري)
    contact_person      varchar(120),
    mobile              varchar(30),
    phone               varchar(30),
    email               varchar(120),
    address             varchar(300),
    payment_terms_days  smallint      NOT NULL DEFAULT 30,
    currency_code       char(3)       NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    vat_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- ض.ق.م (إضافة)
    wht_tax_code_id     integer       REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,   -- خصم المنبع (خصم)
    bank_name           varchar(120),
    bank_account        varchar(60),                                   -- رقم الحساب / IBAN
    notes               varchar(500),
    is_system           boolean       NOT NULL DEFAULT false,
    is_active           boolean       NOT NULL DEFAULT true,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    row_version         integer       NOT NULL DEFAULT 1 CONSTRAINT suppliers_row_version_chk CHECK (row_version > 0),
    CONSTRAINT suppliers_seq_unique UNIQUE (seq),
    CONSTRAINT suppliers_seq_range CHECK (seq > 0 OR (seq = 0 AND is_system)),
    CONSTRAINT suppliers_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT suppliers_tax_not_blank CHECK (tax_reg_no IS NULL OR btrim(tax_reg_no) <> ''),
    CONSTRAINT suppliers_terms_range CHECK (payment_terms_days BETWEEN 0 AND 365),
    CONSTRAINT suppliers_system_active CHECK (is_active OR NOT is_system)
);
CREATE UNIQUE INDEX suppliers_code_unique ON party.suppliers (code);
CREATE UNIQUE INDEX suppliers_name_unique ON party.suppliers (lower(btrim(name_ar)));
CREATE UNIQUE INDEX suppliers_tax_unique ON party.suppliers (party.normalize_tax_no(tax_reg_no))
    WHERE tax_reg_no IS NOT NULL;
CREATE INDEX suppliers_group_idx ON party.suppliers (group_id);
CREATE INDEX suppliers_currency_idx ON party.suppliers (currency_code);
CREATE INDEX suppliers_vat_idx ON party.suppliers (vat_tax_code_id);
CREATE INDEX suppliers_wht_idx ON party.suppliers (wht_tax_code_id);
CREATE INDEX suppliers_mobile_idx ON party.suppliers (mobile);
CREATE INDEX suppliers_active_idx ON party.suppliers (is_active, group_id);
COMMENT ON TABLE party.suppliers IS
    'الموردين: بُعد على سطر القيد — حساب الرقابة من المجموعة · الكود SUB-001 · الاسم والرقم الضريبي مايتكرروش';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.suppliers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    g  party.supplier_groups%ROWTYPE;
    t  acc.tax_codes%ROWTYPE;
    c  core.currencies%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_system THEN
            RAISE EXCEPTION 'المورد «%» نظامي (للمشتريات الكاش) — مابيتمسحش.', OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود المورد «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        IF NEW.is_system <> OLD.is_system THEN
            RAISE EXCEPTION '«نظامي» بيتحدد من البرنامج بس.';
        END IF;
        IF OLD.is_system AND NOT NEW.is_active THEN
            RAISE EXCEPTION 'المورد «%» نظامي (للمشتريات الكاش) — مابيتوقفش.', OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- المجموعة: نشطة (FOR SHARE: لو جهاز تاني بيوقفها في نفس اللحظة يستنى).
    IF TG_OP = 'INSERT' OR NEW.group_id IS DISTINCT FROM OLD.group_id THEN
        SELECT * INTO g FROM party.supplier_groups WHERE id = NEW.group_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'مجموعة الموردين مش موجودة (ممكن تكون اتمسحت).';
        END IF;
        IF NOT g.is_active THEN
            RAISE EXCEPTION 'المجموعة «%» موقوفة — مينفعش تختارها لمورد.', g.name_ar;
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
            RAISE EXCEPTION 'ض.ق.م للمورد لازم يبقى كود ضريبة «إضافة» نشط بيتطبق على المشتريات.';
        END IF;
    END IF;

    -- خصم المنبع: كود «خصم» نشط بيتطبق على المشتريات.
    IF NEW.wht_tax_code_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.wht_tax_code_id IS DISTINCT FROM OLD.wht_tax_code_id) THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.wht_tax_code_id FOR SHARE;
        IF NOT FOUND OR NOT t.is_active OR t.kind <> 'deduct' OR t.applies_to NOT IN ('purch', 'both') THEN
            RAISE EXCEPTION 'خصم المنبع للمورد لازم يبقى كود ضريبة «خصم» نشط بيتطبق على المشتريات.';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER suppliers_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON party.suppliers
    FOR EACH ROW EXECUTE FUNCTION party.suppliers_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON party.suppliers
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- المجموعة اللي فيها موردين: مابتتمسحش (رسالة عربي بدل خطأ الـ FK).
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.supplier_groups_suppliers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM party.suppliers WHERE group_id = OLD.id;
    IF n > 0 THEN
        RAISE EXCEPTION 'المجموعة «%» فيها % مورد — انقلهم لمجموعة تانية أو وقّف المجموعة بدل ما تمسحها.',
            OLD.name_ar, n;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER supplier_groups_zsuppliers_trg
    BEFORE DELETE ON party.supplier_groups
    FOR EACH ROW EXECUTE FUNCTION party.supplier_groups_suppliers_guard();

-- ---------------------------------------------------------------------------
-- كود الضريبة المربوط بمورد: مايتمسحش ولا يتوقف ولا يتغيّر نوعه أو يبطل يتطبق على المشتريات.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.tax_codes_suppliers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM party.suppliers WHERE OLD.id IN (vat_tax_code_id, wht_tax_code_id);
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'كود الضريبة «%» متسجل على % مورد — غيّره عندهم الأول.', OLD.code, n;
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR NEW.kind <> OLD.kind
       OR NEW.applies_to NOT IN ('purch', 'both') THEN
        RAISE EXCEPTION 'كود الضريبة «%» متسجل على % مورد — مينفعش يتوقف ولا يتغيّر نوعه.', OLD.code, n;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER tax_codes_zsuppliers_links_trg
    BEFORE UPDATE OR DELETE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION acc.tax_codes_suppliers_guard();

-- ---------------------------------------------------------------------------
-- «مورد نقدي» (SUB-000) للمشتريات الكاش — في المجموعة النظامية «موردين — عام» · نقدي (0 يوم) · العملة الأساسية ·
-- ض.ق.م المجموعة · من غير خصم منبع ولا رقم ضريبي.
-- ---------------------------------------------------------------------------
INSERT INTO party.suppliers (seq, name_ar, name_en, group_id, payment_terms_days, currency_code,
                             vat_tax_code_id, wht_tax_code_id, notes, is_system)
SELECT 0, 'مورد نقدي', 'Cash Supplier', g.id, 0,
       coalesce((SELECT code FROM core.currencies WHERE is_base LIMIT 1), g.currency_code),
       g.vat_tax_code_id, NULL, 'للمشتريات الكاش من غير بيانات مورد', true
FROM party.supplier_groups g
WHERE g.is_system
ORDER BY g.code::bigint
LIMIT 1;
