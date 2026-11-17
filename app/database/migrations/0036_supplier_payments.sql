-- 0036: «سند الصرف» بقى **للمورد** بدل العميل (قرار المستخدم 2026-11-17).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرار المستخدم (رد 2026-11-17): «سند القبض للعميل وسند الصرف للمورد» — هما دول سندات العملاء والموردين
-- (مفيش شاشات «سند قبض الموردين / سند صرف الموردين» لوحدها عشان مانكررش).
--   * «سند قبض» = `cash.customer_receipts` (زي ما هو من 0035 — RV).
--   * «سند صرف» = `cash.supplier_payments` **جديد** (نفس شكل سند القبض بالظبط بس المورد بدل العميل — PV):
--     القيد = مدين الموردين (حساب رقابة مجموعة المورد + بُعد المورد) · دائن الخزنة / البنك.
--   * `cash.customer_payments` «سند صرف العملاء» (0035) بيتشال: لو فاضي بيتمسح هو ونوعه وقواعده؛ ولو فيه سندات
--     (حد جرّبه على الديسكتوب) بيفضل جدول قديم بحمايته من غير شاشة، ونوعه بياخد البادئة CPV عشان PV تبقى لسند الصرف.
-- الحماية زي 0035 بالظبط — الدوال بقت عامة: الطرف (عميل / مورد) من اسم الجدول.

-- ---------------------------------------------------------------------------
-- 1) نوع الجدول: نوع المستند + اسمه + الطرف (عمود وجدول ومسمّى)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.voucher_kind(p_table text, OUT doc_type text, OUT label text, OUT party_col text,
                                  OUT party_table text, OUT party_label text)
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_table WHEN 'customer_receipts' THEN 'customer_receipt'
                        WHEN 'supplier_payments' THEN 'supplier_payment' ELSE 'customer_payment' END,
           CASE p_table WHEN 'customer_receipts' THEN 'سند القبض'
                        WHEN 'supplier_payments' THEN 'سند الصرف' ELSE 'سند صرف العملاء' END,
           CASE p_table WHEN 'supplier_payments' THEN 'supplier_id' ELSE 'customer_id' END,
           CASE p_table WHEN 'supplier_payments' THEN 'party.suppliers' ELSE 'party.customers' END,
           CASE p_table WHEN 'supplier_payments' THEN 'المورد' ELSE 'العميل' END
$$;

-- ---------------------------------------------------------------------------
-- 2) الحماية (دالة واحدة لكل جداول السندات)
-- ---------------------------------------------------------------------------
ALTER FUNCTION cash.customer_vouchers_guard() RENAME TO vouchers_guard;
ALTER FUNCTION cash.customer_vouchers_entry_check() RENAME TO vouchers_entry_check;

CREATE OR REPLACE FUNCTION cash.vouchers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k        record := cash.voucher_kind(TG_TABLE_NAME);
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
    m        record;
    base     text;
    pid      integer;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := k.label || ' ' || coalesce(OLD.voucher_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — سنداتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.voucher_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    -- السنة المالية من التاريخ (لازم مفتوحة).
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.voucher_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.voucher_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها سند.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.voucher_no IS NOT NULL THEN
            RAISE EXCEPTION 'السند الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.voucher_no IS NOT NULL AND NEW.voucher_no IS DISTINCT FROM OLD.voucher_no) THEN
            RAISE EXCEPTION 'رقم السند ثابت.';
        END IF;
        IF OLD.voucher_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمه من سنة % — تاريخه لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION '% مرحّل — مايتعدّلش (الغي ترحيله الأول).', lbl;
                END IF;
                RETURN NEW;
            END IF;
            -- إلغاء الترحيل: الحالة بس (السند بيرجع مسودة برقمه).
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع السند مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NEW.voucher_no IS NULL THEN
                NEW.voucher_no := core.next_document_no(k.doc_type, NEW.voucher_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- الطرف (العميل / المورد) · الخزنة / البنك · أمر التوريد: وقت الإضافة أو التغيير أو الترحيل.
    pid := (to_jsonb(NEW) ->> k.party_col)::integer;
    IF TG_OP = 'INSERT' OR posting OR pid IS DISTINCT FROM (to_jsonb(OLD) ->> k.party_col)::integer THEN
        EXECUTE format('SELECT code, name_ar, is_active FROM %s WHERE id = $1 FOR SHARE', k.party_table)
            INTO m USING pid;
        IF m IS NULL THEN
            RAISE EXCEPTION '% مش موجود (ممكن يكون اتمسح) — دوس «تحديث».', k.party_label;
        END IF;
        IF NOT m.is_active THEN
            RAISE EXCEPTION '% «% %» موقوف — اختار % نشط.', k.party_label, m.code, m.name_ar,
                CASE k.party_col WHEN 'supplier_id' THEN 'مورد' ELSE 'عميل' END;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR posting OR NEW.treasury_id IS DISTINCT FROM OLD.treasury_id
       OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id THEN
        SELECT code INTO base FROM core.currencies WHERE is_base;
        IF NEW.treasury_id IS NOT NULL THEN
            SELECT code, name_ar, is_active, currency_code INTO m FROM cash.treasuries WHERE id = NEW.treasury_id FOR SHARE;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'الخزنة «% %» موقوفة — اختار خزنة نشطة.', m.code, m.name_ar;
            END IF;
        ELSE
            SELECT ba.code, b.name_ar, ba.is_active, ba.currency_code INTO m
              FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id
             WHERE ba.id = NEW.bank_account_id FOR SHARE OF ba;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'الحساب البنكي «% %» موقوف — اختار حساب نشط.', m.code, m.name_ar;
            END IF;
        END IF;
        IF m.currency_code IS DISTINCT FROM base THEN
            RAISE EXCEPTION '«% %» بعملة % — السند بالعملة الأساسية (%) بس.', m.code, m.name_ar, m.currency_code, base;
        END IF;
    END IF;
    IF NEW.project_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR posting OR NEW.project_id IS DISTINCT FROM OLD.project_id) THEN
        SELECT code, status INTO m FROM acc.projects WHERE id = NEW.project_id;
        IF m.status <> 'open' THEN
            RAISE EXCEPTION 'أمر التوريد «%» مش جاري (مقفول أو ملغي).', m.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- المرحّل لازم ليه قيد مرحّل بنفس المبلغ · المسودة مالهاش قيد (آخر الـ transaction).
CREATE OR REPLACE FUNCTION cash.vouchers_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k  record := cash.voucher_kind(TG_TABLE_NAME);
    v  record;
    e  record;
BEGIN
    EXECUTE format('SELECT id, status, amount, voucher_no, draft_no FROM %I.%I WHERE id = $1',
                   TG_TABLE_SCHEMA, TG_TABLE_NAME) INTO v USING NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = k.doc_type AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION '% % مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', k.label, v.voucher_no;
        END IF;
        IF e.total_debit <> v.amount THEN
            RAISE EXCEPTION 'قيد % % مبلغه % مش زي مبلغ السند %.', k.label, v.voucher_no, e.total_debit, v.amount;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION '% % مسودة وليه قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.', k.label,
            coalesce(v.voucher_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

DROP FUNCTION cash.customer_voucher_kind(text);

-- ---------------------------------------------------------------------------
-- 3) السنة المالية: نفس الحماية لكل جداول السندات الموجودة
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  text;
    c  integer;
    n  integer := 0;
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t IN ARRAY ARRAY['cash.customer_receipts', 'cash.supplier_payments', 'cash.customer_payments'] LOOP
            CONTINUE WHEN to_regclass(t) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND voucher_date NOT BETWEEN $2 AND $3', t)
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.', n, OLD.year_code;
        END IF;
    END IF;
    IF OLD.status = 'open' AND NEW.status = 'closed' THEN
        n := 0;
        FOREACH t IN ARRAY ARRAY['cash.customer_receipts', 'cash.supplier_payments', 'cash.customer_payments'] LOOP
            CONTINUE WHEN to_regclass(t) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND status = ''draft''', t)
                INTO c USING OLD.id;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة — رحّلهم أو امسحهم قبل القفل.', OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------
-- 4) «سند صرف العملاء» بيتشال (لو فاضي) — وإلا بيفضل قديم ونوعه بياخد CPV
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cash.customer_payments) THEN
        DROP TABLE cash.customer_payments;                         -- الـ sequence بتاعه بيتمسح معاه (OWNED BY)
        DELETE FROM core.document_sequences WHERE doc_type = 'customer_payment';
        DELETE FROM core.document_types WHERE code = 'customer_payment';
        DELETE FROM acc.posting_doc_types WHERE code = 'customer_payment';   -- قواعده بتتمسح معاه (CASCADE)
    ELSE
        UPDATE core.document_types SET prefix = 'CPV', name_ar = 'سند صرف العملاء (قديم)' WHERE code = 'customer_payment';
        UPDATE acc.posting_doc_types SET name_ar = 'سند صرف العملاء (قديم)', sort_order = 12 WHERE code = 'customer_payment';
        COMMENT ON TABLE cash.customer_payments IS
            'سند صرف العملاء (قديم — 0035): اتشال من الشاشات (سند الصرف بقى للمورد في cash.supplier_payments)';
    END IF;
END $$;

UPDATE core.document_types SET name_ar = 'سند قبض' WHERE code = 'customer_receipt';
UPDATE acc.posting_doc_types SET name_ar = 'سند قبض (العملاء)' WHERE code = 'customer_receipt';

-- ---------------------------------------------------------------------------
-- 5) «سند صرف» للمورد: الترقيم + نوع المستند + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('supplier_payment', 'سند صرف', 'PV', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('supplier_payment', 'سند صرف (الموردين)', 11);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('supplier_payment', 'payable', 'الموردين', 'dr', 'party',    NULL, NULL, 'supplier', 1),
    ('supplier_payment', 'cash',    'الخزينة',  'cr', 'treasury', NULL, NULL, NULL,       2),
    ('supplier_payment', 'bank',    'البنك',    'cr', 'bank',     NULL, NULL, NULL,       3);

-- ---------------------------------------------------------------------------
-- 6) الجدول (نفس شكل سند القبض بالظبط — المورد بدل العميل)
-- ---------------------------------------------------------------------------
CREATE SEQUENCE cash.supplier_payments_draft_seq AS integer;
CREATE TABLE cash.supplier_payments (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    voucher_no       varchar(30),                                   -- PV-2026-00001 (من أول ترحيل)
    draft_no         integer        NOT NULL DEFAULT nextval('cash.supplier_payments_draft_seq'),
    voucher_date     date           NOT NULL,
    fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    supplier_id      integer        NOT NULL REFERENCES party.suppliers (id) ON DELETE RESTRICT,
    source_kind      varchar(10)    NOT NULL,                       -- treasury خزنة · bank حساب بنك
    treasury_id      integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    amount           numeric(18,2)  NOT NULL,
    description      varchar(500)   NOT NULL,
    paper_no         varchar(30),                                   -- رقم الدفتر الورقي (اختياري)
    project_id       integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,
    status           varchar(10)    NOT NULL DEFAULT 'draft',
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at        timestamptz,
    posted_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT supplier_payments_row_version_chk CHECK (row_version > 0),
    CONSTRAINT supplier_payments_no_unique UNIQUE (voucher_no),
    CONSTRAINT supplier_payments_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT supplier_payments_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT supplier_payments_amount_chk CHECK (amount > 0),
    CONSTRAINT supplier_payments_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT supplier_payments_paper_chk CHECK (paper_no IS NULL OR btrim(paper_no) <> ''),
    CONSTRAINT supplier_payments_source_chk CHECK (
        (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
        OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)),
    CONSTRAINT supplier_payments_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR voucher_no IS NOT NULL))
);
ALTER SEQUENCE cash.supplier_payments_draft_seq OWNED BY cash.supplier_payments.draft_no;
COMMENT ON TABLE cash.supplier_payments IS
    'سند صرف: مورد واحد + خزنة أو حساب بنك + المبلغ · مسودة ← مرحّل بقيد أوتوماتيك · الرقم من أول ترحيل';

-- القائمة بالفترة (الأحدث فوق) + index على كل FK وعلى البحث.
CREATE INDEX supplier_payments_date_idx ON cash.supplier_payments (voucher_date DESC, id DESC);
CREATE INDEX supplier_payments_year_idx ON cash.supplier_payments (fiscal_year_id, status);
CREATE INDEX supplier_payments_supplier_idx ON cash.supplier_payments (supplier_id, voucher_date DESC);
CREATE INDEX supplier_payments_treasury_idx ON cash.supplier_payments (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX supplier_payments_bank_idx ON cash.supplier_payments (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX supplier_payments_project_idx ON cash.supplier_payments (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX supplier_payments_created_by_idx ON cash.supplier_payments (created_by);
CREATE INDEX supplier_payments_posted_by_idx ON cash.supplier_payments (posted_by);
CREATE INDEX supplier_payments_desc_idx ON cash.supplier_payments (lower(description) varchar_pattern_ops);
CREATE INDEX supplier_payments_paper_idx ON cash.supplier_payments (paper_no) WHERE paper_no IS NOT NULL;

CREATE TRIGGER supplier_payments_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.supplier_payments
    FOR EACH ROW EXECUTE FUNCTION cash.vouchers_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.supplier_payments
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER supplier_payments_entry_trg AFTER INSERT OR UPDATE ON cash.supplier_payments
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.vouchers_entry_check();

COMMENT ON TABLE cash.customer_receipts IS
    'سند قبض: عميل واحد + خزنة أو حساب بنك + المبلغ · مسودة ← مرحّل بقيد أوتوماتيك · الرقم من أول ترحيل';
