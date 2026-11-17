-- 0035: «سند قبض العملاء» و«سند صرف العملاء» (المرحلة 5 — النموذج 5 «الفورم + كروت الأرقام + القيد»،
--       اختيار المستخدم 2026-11-17 — mockups/phase5/01_vouchers.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (ردود 2026-11-17):
--   * **شاشتين منفصلين وجدولين منفصلين**: `cash.customer_receipts` «سند قبض العملاء» و`cash.customer_payments`
--     «سند صرف العملاء» — للعملاء بس (سندات الموردين والعهد والشركاء بنود لوحدها في الخطة زي الأكسس).
--   * السند **سطر واحد**: عميل واحد + خزنة أو حساب بنك + المبلغ + البيان · **أمر التوريد اختياري**.
--   * **مفيش خصم منبع ولا خصم مسموح / مكتسب** على السند (رد المستخدم: «شيلهم»).
--   * **«حفظ مسودة» و«ترحيل» زي القيد اليدوي** · إلغاء الترحيل بالصلاحية في السنة المفتوحة.
--   * الترقيم **RV-2026-00001** للقبض و**PV-2026-00001** للصرف (يبدأ من 1 كل سنة) — **الرقم بيتاخد مع أول ترحيل**
--     (زي JV) والمسودة ليها رقم مؤقت «مسودة N» · اللي خد رقم مايتمسحش (عشان مايبقاش فيه فجوة) · رقم الدفتر الورقي اختياري.
--   * الخزنة مش مكفية في سند الصرف ← **رسالة تحذير بس** (من الشاشة — مش منع) · الطباعة بعدين.
--   * بالجنيه بس (زي القيود) — الخزنة / الحساب البنكي لازم بالعملة الأساسية.
-- القيد: بمحرك الترحيل (0032) — نوعين مستندات جداد بقواعدهم:
--   القبض = مدين الخزنة / البنك · دائن العملاء (حساب رقابة مجموعة العميل + بُعد العميل).
--   الصرف = مدين العملاء · دائن الخزنة / البنك.
-- الحماية في القاعدة نفسها:
--   * السنة من التاريخ ولازم مفتوحة · المرحّل مايتعدّلش ولا يتمسح (غير إلغاء الترحيل) · الرقم ثابت وفي نفس السنة.
--   * العميل والخزنة / البنك نشطين وقت الإضافة أو التغيير أو الترحيل · أمر التوريد «جاري».
--   * **المرحّل لازم ليه قيد مرحّل بنفس المبلغ والمسودة مالهاش قيد** (Constraint Trigger مؤجل لآخر الـ transaction).
--   * السنة ماتتقفلش وفيها سندات مسودة، وتواريخها ماتسيبش سند برّاها.

-- ---------------------------------------------------------------------------
-- 1) الترقيم + أنواع المستندات + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('customer_receipt', 'سند قبض العملاء', 'RV', 5),
    ('customer_payment', 'سند صرف العملاء', 'PV', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('customer_receipt', 'سند قبض العملاء', 10),
    ('customer_payment', 'سند صرف العملاء', 11);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('customer_receipt', 'cash',       'الخزينة',  'dr', 'treasury', NULL, NULL, NULL,       1),
    ('customer_receipt', 'bank',       'البنك',    'dr', 'bank',     NULL, NULL, NULL,       2),
    ('customer_receipt', 'receivable', 'العملاء',  'cr', 'party',    NULL, NULL, 'customer', 3),
    ('customer_payment', 'receivable', 'العملاء',  'dr', 'party',    NULL, NULL, 'customer', 1),
    ('customer_payment', 'cash',       'الخزينة',  'cr', 'treasury', NULL, NULL, NULL,       2),
    ('customer_payment', 'bank',       'البنك',    'cr', 'bank',     NULL, NULL, NULL,       3);

-- ---------------------------------------------------------------------------
-- 2) الحماية (دالة واحدة للجدولين — النوع من اسم الجدول)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.customer_voucher_kind(p_table text, OUT doc_type text, OUT label text)
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_table WHEN 'customer_receipts' THEN 'customer_receipt' ELSE 'customer_payment' END,
           CASE p_table WHEN 'customer_receipts' THEN 'سند القبض' ELSE 'سند الصرف' END
$$;

CREATE FUNCTION cash.customer_vouchers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k        record := cash.customer_voucher_kind(TG_TABLE_NAME);
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
    m        record;
    base     text;
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

    -- العميل · الخزنة / البنك · أمر التوريد: وقت الإضافة أو التغيير أو الترحيل.
    IF TG_OP = 'INSERT' OR posting OR NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
        SELECT code, name_ar, is_active INTO m FROM party.customers WHERE id = NEW.customer_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'العميل مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
        END IF;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'العميل «% %» موقوف — اختار عميل نشط.', m.code, m.name_ar;
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

-- المرحّل لازم ليه قيد مرحّل بنفس المبلغ · المسودة مالهاش قيد (آخر الـ transaction — بعد ما المحرك يعمل / يمسح القيد).
CREATE FUNCTION cash.customer_vouchers_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k  record := cash.customer_voucher_kind(TG_TABLE_NAME);
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

-- ---------------------------------------------------------------------------
-- 3) الجدولين (نفس الشكل بالظبط)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t     text;
    title text;
BEGIN
    FOREACH t IN ARRAY ARRAY['customer_receipts', 'customer_payments'] LOOP
        title := CASE t WHEN 'customer_receipts' THEN 'سند قبض العملاء' ELSE 'سند صرف العملاء' END;
        EXECUTE format('CREATE SEQUENCE cash.%1$s_draft_seq AS integer', t);
        EXECUTE format($sql$
            CREATE TABLE cash.%1$s (
                id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                voucher_no       varchar(30),                                   -- RV/PV-2026-00001 (من أول ترحيل)
                draft_no         integer        NOT NULL DEFAULT nextval('cash.%1$s_draft_seq'),
                voucher_date     date           NOT NULL,
                fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
                customer_id      integer        NOT NULL REFERENCES party.customers (id) ON DELETE RESTRICT,
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
                row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT %1$s_row_version_chk CHECK (row_version > 0),
                CONSTRAINT %1$s_no_unique UNIQUE (voucher_no),
                CONSTRAINT %1$s_draft_no_unique UNIQUE (draft_no),
                CONSTRAINT %1$s_status_chk CHECK (status IN ('draft', 'posted')),
                CONSTRAINT %1$s_amount_chk CHECK (amount > 0),
                CONSTRAINT %1$s_desc_chk CHECK (btrim(description) <> ''),
                CONSTRAINT %1$s_paper_chk CHECK (paper_no IS NULL OR btrim(paper_no) <> ''),
                CONSTRAINT %1$s_source_chk CHECK (
                    (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
                    OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)),
                CONSTRAINT %1$s_posted_chk CHECK (
                    (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR voucher_no IS NOT NULL))
            )$sql$, t);
        EXECUTE format('ALTER SEQUENCE cash.%1$s_draft_seq OWNED BY cash.%1$s.draft_no', t);
        -- القائمة بالفترة (الأحدث فوق) + index على كل FK وعلى البحث.
        EXECUTE format('CREATE INDEX %1$s_date_idx ON cash.%1$s (voucher_date DESC, id DESC)', t);
        EXECUTE format('CREATE INDEX %1$s_year_idx ON cash.%1$s (fiscal_year_id, status)', t);
        EXECUTE format('CREATE INDEX %1$s_customer_idx ON cash.%1$s (customer_id, voucher_date DESC)', t);
        EXECUTE format('CREATE INDEX %1$s_treasury_idx ON cash.%1$s (treasury_id) WHERE treasury_id IS NOT NULL', t);
        EXECUTE format('CREATE INDEX %1$s_bank_idx ON cash.%1$s (bank_account_id) WHERE bank_account_id IS NOT NULL', t);
        EXECUTE format('CREATE INDEX %1$s_project_idx ON cash.%1$s (project_id) WHERE project_id IS NOT NULL', t);
        EXECUTE format('CREATE INDEX %1$s_created_by_idx ON cash.%1$s (created_by)', t);
        EXECUTE format('CREATE INDEX %1$s_posted_by_idx ON cash.%1$s (posted_by)', t);
        EXECUTE format('CREATE INDEX %1$s_desc_idx ON cash.%1$s (lower(description) varchar_pattern_ops)', t);
        EXECUTE format('CREATE INDEX %1$s_paper_idx ON cash.%1$s (paper_no) WHERE paper_no IS NOT NULL', t);
        EXECUTE format('COMMENT ON TABLE cash.%1$s IS %2$L', t,
                       title || ': عميل واحد + خزنة أو حساب بنك + المبلغ · مسودة ← مرحّل بقيد أوتوماتيك · الرقم من أول ترحيل');
        EXECUTE format('CREATE TRIGGER %1$s_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.%1$s '
                       'FOR EACH ROW EXECUTE FUNCTION cash.customer_vouchers_guard()', t);
        EXECUTE format('CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.%1$s '
                       'FOR EACH ROW EXECUTE FUNCTION core.bump_row_version()', t);
        EXECUTE format('CREATE CONSTRAINT TRIGGER %1$s_entry_trg AFTER INSERT OR UPDATE ON cash.%1$s '
                       'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.customer_vouchers_entry_check()', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 4) السنة المالية: ماتتقفلش وفيها سندات مسودة · تواريخها ماتسيبش سند برّاها
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        SELECT (SELECT count(*) FROM cash.customer_receipts
                 WHERE fiscal_year_id = OLD.id AND voucher_date NOT BETWEEN NEW.start_date AND NEW.end_date)
             + (SELECT count(*) FROM cash.customer_payments
                 WHERE fiscal_year_id = OLD.id AND voucher_date NOT BETWEEN NEW.start_date AND NEW.end_date) INTO n;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.', n, OLD.year_code;
        END IF;
    END IF;
    IF OLD.status = 'open' AND NEW.status = 'closed' THEN
        SELECT (SELECT count(*) FROM cash.customer_receipts WHERE fiscal_year_id = OLD.id AND status = 'draft')
             + (SELECT count(*) FROM cash.customer_payments WHERE fiscal_year_id = OLD.id AND status = 'draft') INTO n;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة — رحّلهم أو امسحهم قبل القفل.', OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fiscal_years_zvoucher_guard_trg
    BEFORE UPDATE ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_voucher_guard();
