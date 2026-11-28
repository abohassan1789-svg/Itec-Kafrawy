-- 0054: «تحصيل العملاء + الدفعات المقدمة» (المرحلة 8) — تحديث «سند القبض» بنفس فكرة «سند الصرف» في 0050:
--       **عملة للسند** + **ربطه بفواتير البيع** + **فروق العملة**.
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرار المستخدم (2026-11-28): «نعمل برضه سند القبض؛ نفس الفكرة اللي إحنا عملناها في سند الصرف هننفذها في سند
-- القبض … مش هيكون بقى في حاجة اسمها سندات العملة» ← مفيش شاشة جديدة ولا شاشة عملات لوحدها: «سند القبض» نفسه
-- بيتحدّث، والعملة بتتختار جوه السند (من غير 10 نماذج — نفس تصميم سند الصرف).
--   * **السند بعملة** و«استلمنا في» = **خزنة أو حساب بنك بنفس العملة بس** (البنك زي الخزنة).
--   * اختيار الفواتير من نافذة «📋 الفواتير» **بالتوتال**: الإجمالي − **المرتجع الآجل المربوط** − المحصّل = المتبقي.
--   * **كل فاتورة بتتحصّل بعملتها بس** (الفاتورة «متعددة العملات» = جزء لكل عملة).
--   * «وزّع الأقدم الأول» + تعديل · اللي مااتوزّعش = **دفعة مقدمة على حساب العميل بنفس العملة**.
--   * **فرق العملة بيتقيّد مع السند**: العميل بيتقفل بسعر الفاتورة · الخزنة / البنك بسعر السند · الفرق على
--     «فروق عملة دائنة» (استلمنا جنيه أكتر) أو «فروق عملة مدينة» (استلمنا أقل).
-- اللي في السكريبت:
--   1) `cash.customer_receipts`: `currency_code` + `fx_rate` + `amount_base` (بالجنيه = المبلغ × السعر).
--   2) `cash.vouchers_guard` بقت عامة للسندين بعملة (القبض والصرف) + `cash.voucher_has_allocations`.
--   3) `cash.receipt_allocations`: السند · فاتورة البيع · المبلغ بالعملة · سعر الفاتورة · المبلغ بالجنيه بسعرها.
--      (جدول لوحده جنب `cash.voucher_allocations` بتاع سند الصرف — نفس الشكل بالظبط بس FK على فواتير البيع.)
--   4) `sales.sales_invoice_dues`: المستحق على كل (فاتورة بيع آجلة مرحّلة + عملة).
--   5) الحماية: الفاتورة لنفس العميل · الموزّع ≤ مبلغ السند · **المحصّل ≤ المتبقي بقفل الفاتورة** (جهازين
--      بيحصّلوا نفس الفاتورة ← التاني يترفض) · قيد السند: مدين الخزنة = المبلغ بالجنيه · دائن العميل = التوزيع
--      بسعر الفواتير + الباقي بسعر السند · **فاتورة البيع مايتلغيش ترحيلها ولا تتمسح وعليها تحصيل مرحّل**.
--   6) قواعد قيد فروق العملة مع سند القبض.
-- المسودة **مابتخصمش** من المتبقي على الفاتورة — المرحّل بس (زي سند الصرف).

-- ---------------------------------------------------------------------------
-- 1) عملة سند القبض
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    base text := (SELECT code FROM core.currencies WHERE is_base);
BEGIN
    -- الافتراضي الثابت بيملا السندات القديمة من غير ما يشغّل تريجر الحماية (المرحّل مايتعدّلش).
    EXECUTE format('ALTER TABLE cash.customer_receipts '
                   'ADD COLUMN currency_code char(3) NOT NULL DEFAULT %L '
                   '    REFERENCES core.currencies (code) ON DELETE RESTRICT, '
                   'ADD COLUMN fx_rate numeric(18,6) NOT NULL DEFAULT 1, '
                   'ADD COLUMN amount_base numeric(18,2)', base);
END $$;
ALTER TABLE cash.customer_receipts ALTER COLUMN currency_code DROP DEFAULT;
ALTER TABLE cash.customer_receipts DISABLE TRIGGER USER;
UPDATE cash.customer_receipts SET amount_base = amount;
ALTER TABLE cash.customer_receipts ENABLE TRIGGER USER;
ALTER TABLE cash.customer_receipts
    ALTER COLUMN amount_base SET NOT NULL,
    ADD CONSTRAINT customer_receipts_rate_chk CHECK (fx_rate > 0),
    ADD CONSTRAINT customer_receipts_base_chk CHECK (amount_base = round(amount * fx_rate, 2));
CREATE INDEX customer_receipts_currency_idx ON cash.customer_receipts (currency_code);
COMMENT ON COLUMN cash.customer_receipts.currency_code IS 'عملة السند = عملة الخزنة / البنك = عملة الفواتير اللي بيحصّلها';
COMMENT ON COLUMN cash.customer_receipts.fx_rate IS 'سعر الصرف بتاريخ السند (core.rate_on ويتعدّل) — الأساسية = 1';
COMMENT ON COLUMN cash.customer_receipts.amount_base IS 'المبلغ بالجنيه = المبلغ × السعر (اللي دخل الخزنة / البنك)';
COMMENT ON TABLE cash.customer_receipts IS
    'سند قبض: عميل + عملة + خزنة أو بنك بنفس العملة + المبلغ · بيتوزّع على فواتير البيع (cash.receipt_allocations) · '
    'مسودة ← مرحّل بقيد أوتوماتيك (+ فروق العملة) · الرقم من أول ترحيل';

-- ---------------------------------------------------------------------------
-- 2) جدول توزيع سند القبض على فواتير البيع
-- ---------------------------------------------------------------------------
CREATE TABLE cash.receipt_allocations (
    id                   bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_receipt_id  integer        NOT NULL REFERENCES cash.customer_receipts (id) ON DELETE CASCADE,
    sales_invoice_id     integer        NOT NULL REFERENCES sales.sales_invoices (id) ON DELETE RESTRICT,
    currency_code        char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,   -- = عملة السند
    amount               numeric(18,2)  NOT NULL,                 -- بعملة السند (= عملة جزء الفاتورة)
    invoice_rate         numeric(18,6)  NOT NULL DEFAULT 1,       -- سعر الفاتورة لجزء العملة ده (بالجنيه ÷ بالعملة)
    amount_base          numeric(18,2)  NOT NULL,                 -- بالجنيه بسعر الفاتورة = اللي اتقفل من حساب العميل
    created_by           integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at           timestamptz    NOT NULL DEFAULT now(),
    CONSTRAINT receipt_allocations_unique UNIQUE (customer_receipt_id, sales_invoice_id),
    CONSTRAINT receipt_allocations_amount_chk CHECK (amount > 0 AND amount_base >= 0 AND invoice_rate > 0)
);
COMMENT ON TABLE cash.receipt_allocations IS
    'توزيع سند القبض على فواتير البيع: المبلغ بعملة السند + بالجنيه بسعر الفاتورة (منه فرق العملة وأعمار الديون) — '
    'اللي مااتوزّعش من السند = دفعة مقدمة على حساب العميل';
CREATE INDEX receipt_allocations_invoice_idx ON cash.receipt_allocations (sales_invoice_id);
CREATE INDEX receipt_allocations_currency_idx ON cash.receipt_allocations (currency_code);
CREATE INDEX receipt_allocations_created_by_idx ON cash.receipt_allocations (created_by);

-- السند ليه توزيع؟ (سند الصرف ← cash.voucher_allocations · سند القبض ← cash.receipt_allocations)
CREATE FUNCTION cash.voucher_has_allocations(p_table text, p_id integer) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT CASE p_table
        WHEN 'supplier_payments' THEN EXISTS (SELECT 1 FROM cash.voucher_allocations WHERE supplier_payment_id = p_id)
        WHEN 'customer_receipts' THEN EXISTS (SELECT 1 FROM cash.receipt_allocations WHERE customer_receipt_id = p_id)
        ELSE false END
$$;

-- ---------------------------------------------------------------------------
-- 3) الحماية: العملة + عملة الخزنة / البنك للسندين (القبض والصرف) — «سند صرف العملاء» القديم بالأساسية بس
-- ---------------------------------------------------------------------------
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
    cur      text;
    pid      integer;
    has_cur  boolean := TG_TABLE_NAME IN ('supplier_payments', 'customer_receipts');
    cur_changed boolean := false;
    party_word text := CASE k.party_col WHEN 'supplier_id' THEN 'المورد' ELSE 'العميل' END;
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
    SELECT code INTO base FROM core.currencies WHERE is_base;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.voucher_no IS NOT NULL THEN
            RAISE EXCEPTION 'السند الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
        IF has_cur THEN                                      -- السند من غير عملة = العملة الأساسية
            NEW.currency_code := coalesce(NEW.currency_code, base);
            NEW.fx_rate := coalesce(NEW.fx_rate, 1);
            NEW.amount_base := round(NEW.amount * NEW.fx_rate, 2);
        END IF;
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
        IF has_cur THEN
            NEW.amount_base := round(NEW.amount * NEW.fx_rate, 2);
            cur_changed := NEW.currency_code IS DISTINCT FROM OLD.currency_code;
        END IF;
    END IF;

    -- العملة (القبض والصرف): نشطة · الأساسية سعرها 1.
    IF has_cur THEN
        cur := NEW.currency_code;
        IF cur = base AND NEW.fx_rate <> 1 THEN
            RAISE EXCEPTION 'العملة الأساسية (%) سعرها 1.', base;
        END IF;
        IF TG_OP = 'INSERT' OR posting OR cur_changed THEN
            IF NOT (SELECT is_active FROM core.currencies WHERE code = cur) THEN
                RAISE EXCEPTION 'العملة % موقوفة — اختار عملة نشطة.', cur;
            END IF;
        END IF;
        IF cur_changed AND cash.voucher_has_allocations(TG_TABLE_NAME, NEW.id) THEN
            RAISE EXCEPTION 'السند موزّع على فواتير بعملة % — شيل التوزيع الأول عشان تغيّر العملة.', OLD.currency_code;
        END IF;
    ELSE
        cur := base;
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
        IF TG_OP = 'UPDATE' AND has_cur AND pid IS DISTINCT FROM (to_jsonb(OLD) ->> k.party_col)::integer
           AND cash.voucher_has_allocations(TG_TABLE_NAME, NEW.id) THEN
            RAISE EXCEPTION 'السند موزّع على فواتير % القديم — شيل التوزيع الأول عشان تغيّر %.', party_word, party_word;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR posting OR cur_changed OR NEW.treasury_id IS DISTINCT FROM OLD.treasury_id
       OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id THEN
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
        IF m.currency_code IS DISTINCT FROM cur THEN
            IF has_cur THEN
                RAISE EXCEPTION '«% %» بعملة % — والسند بعملة %؛ اختار خزنة أو بنك بنفس عملة السند.',
                    m.code, m.name_ar, m.currency_code, cur;
            END IF;
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

-- السطر: السند مسودة (المرحّل توزيعه مايتغيّرش) · الفاتورة لنفس العميل · العملة = عملة السند.
CREATE FUNCTION cash.receipt_allocations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p    record;
    i    record;
    lbl  text;
    rid  integer := CASE WHEN TG_OP = 'DELETE' THEN OLD.customer_receipt_id ELSE NEW.customer_receipt_id END;
BEGIN
    SELECT v.id, v.status, v.voucher_no, v.draft_no, v.customer_id, v.currency_code, y.status AS year_status,
           y.year_code INTO p
      FROM cash.customer_receipts v JOIN core.fiscal_years y ON y.id = v.fiscal_year_id WHERE v.id = rid;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;     -- السند اتمسح (CASCADE)
    END IF;
    lbl := 'سند القبض ' || coalesce(p.voucher_no, 'مسودة ' || p.draft_no);
    IF p.year_status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — توزيع سنداتها مايتغيّرش.', p.year_code;
    END IF;
    IF p.status = 'posted' THEN
        RAISE EXCEPTION '% مرحّل — توزيعه مايتغيّرش (الغي ترحيله الأول).', lbl;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.customer_receipt_id <> OLD.customer_receipt_id THEN
        RAISE EXCEPTION 'سطر التوزيع مايتنقلش لسند تاني.';
    END IF;
    SELECT coalesce(invoice_no, 'مسودة ' || draft_no) AS label, customer_id INTO i
      FROM sales.sales_invoices WHERE id = NEW.sales_invoice_id;
    IF i.customer_id IS DISTINCT FROM p.customer_id THEN
        RAISE EXCEPTION 'فاتورة البيع % لعميل تاني — السند بيتوزّع على فواتير عميله بس.', i.label;
    END IF;
    NEW.currency_code := p.currency_code;
    RETURN NEW;
END $$;
CREATE TRIGGER receipt_allocations_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.receipt_allocations
    FOR EACH ROW EXECUTE FUNCTION cash.receipt_allocations_guard();

-- ---------------------------------------------------------------------------
-- 4) المستحق على كل (فاتورة بيع آجلة مرحّلة + عملة): الإجمالي − المرتجع الآجل المربوط − التحصيل المرحّل
-- ---------------------------------------------------------------------------
CREATE VIEW sales.sales_invoice_dues AS
SELECT i.id AS invoice_id, i.invoice_no, i.invoice_date, i.due_date, i.customer_id, i.customer_ref,
       i.stock_kind, i.currency_mode, i.project_id,
       t.currency_code, t.total_amount, t.total_base,
       coalesce(r.returned_amount, 0) AS returned_amount, coalesce(r.returned_base, 0) AS returned_base,
       coalesce(p.paid_amount, 0) AS paid_amount, coalesce(p.paid_base, 0) AS paid_base,
       t.total_amount - coalesce(r.returned_amount, 0) - coalesce(p.paid_amount, 0) AS remaining_amount,
       t.total_base - coalesce(r.returned_base, 0) - coalesce(p.paid_base, 0) AS remaining_base
  FROM sales.sales_invoices i
  JOIN LATERAL (SELECT l.currency_code, sum(l.total_amount) AS total_amount, sum(l.total_base) AS total_base
                  FROM sales.sales_invoice_lines l WHERE l.invoice_id = i.id GROUP BY l.currency_code) t ON true
  LEFT JOIN LATERAL (SELECT sum(rl.total_amount) AS returned_amount, sum(rl.total_base) AS returned_base
                       FROM sales.sales_invoice_lines sl
                       JOIN sales.sales_return_lines rl ON rl.source_line_id = sl.id
                       JOIN sales.sales_returns rt ON rt.id = rl.return_id
                      WHERE sl.invoice_id = i.id AND rl.currency_code = t.currency_code
                        AND rt.status = 'posted' AND rt.pay_kind = 'credit') r ON true
  LEFT JOIN LATERAL (SELECT sum(a.amount) AS paid_amount, sum(a.amount_base) AS paid_base
                       FROM cash.receipt_allocations a
                       JOIN cash.customer_receipts cr ON cr.id = a.customer_receipt_id
                      WHERE a.sales_invoice_id = i.id AND a.currency_code = t.currency_code
                        AND cr.status = 'posted') p ON true
 WHERE i.status = 'posted' AND i.pay_kind = 'credit';
COMMENT ON VIEW sales.sales_invoice_dues IS
    'المستحق على فواتير البيع الآجلة المرحّلة لكل عملة: الإجمالي − المرتجع الآجل المربوط − التحصيل المرحّل = المتبقي '
    '(بالعملة وبالجنيه بسعر الفاتورة) — المسودات مابتخصمش';

-- ---------------------------------------------------------------------------
-- 5) آخر الـ transaction: التوزيع صح + القيد مطابق
-- ---------------------------------------------------------------------------
-- (أ) الموزّع ≤ مبلغ السند · والمرحّل: الفاتورة مرحّلة آجلة ولنفس العميل وفيها العملة · المحصّل ≤ المتبقي (بقفل الفاتورة).
CREATE FUNCTION cash.receipt_allocations_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    rid  integer;
    p    record;
    a    record;
    tot  numeric;
    lbl  text;
BEGIN
    IF TG_TABLE_NAME = 'customer_receipts' THEN
        rid := NEW.id;
    ELSE
        rid := CASE WHEN TG_OP = 'DELETE' THEN OLD.customer_receipt_id ELSE NEW.customer_receipt_id END;
    END IF;
    SELECT id, status, voucher_no, draft_no, customer_id, currency_code, amount INTO p
      FROM cash.customer_receipts WHERE id = rid;
    IF NOT FOUND THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    lbl := 'سند القبض ' || coalesce(p.voucher_no, 'مسودة ' || p.draft_no);
    SELECT coalesce(sum(amount), 0) INTO tot FROM cash.receipt_allocations WHERE customer_receipt_id = rid;
    IF tot > p.amount THEN
        RAISE EXCEPTION '%: الموزّع على الفواتير (%) أكتر من مبلغ السند (%).', lbl, tot, p.amount;
    END IF;
    IF p.status <> 'posted' OR tot = 0 THEN
        RETURN NULL;
    END IF;
    -- قفل الفواتير بترتيب ثابت: جهازين بيحصّلوا نفس الفاتورة ← التاني بيستنى وبعدين بيشوف تحصيل الأول.
    PERFORM 1 FROM sales.sales_invoices
      WHERE id IN (SELECT sales_invoice_id FROM cash.receipt_allocations WHERE customer_receipt_id = rid)
      ORDER BY id FOR UPDATE;
    FOR a IN
        SELECT al.amount, al.currency_code, i.invoice_no, i.draft_no, i.status, i.pay_kind, i.customer_id,
               d.total_amount, d.returned_amount, d.paid_amount
          FROM cash.receipt_allocations al
          JOIN sales.sales_invoices i ON i.id = al.sales_invoice_id
          LEFT JOIN sales.sales_invoice_dues d ON d.invoice_id = al.sales_invoice_id
                                              AND d.currency_code = al.currency_code
         WHERE al.customer_receipt_id = rid ORDER BY i.id
    LOOP
        IF a.currency_code <> p.currency_code THEN
            RAISE EXCEPTION '%: سطر التوزيع على % بعملة % والسند بعملة %.', lbl, a.invoice_no, a.currency_code,
                p.currency_code;
        END IF;
        IF a.customer_id <> p.customer_id THEN
            RAISE EXCEPTION '%: فاتورة البيع % لعميل تاني.', lbl, a.invoice_no;
        END IF;
        IF a.status <> 'posted' THEN
            RAISE EXCEPTION '%: فاتورة البيع % مش مرحّلة — مايتحصّلش عليها.', lbl,
                coalesce(a.invoice_no, 'مسودة ' || a.draft_no);
        END IF;
        IF a.pay_kind <> 'credit' THEN
            RAISE EXCEPTION '%: فاتورة البيع % نقدية — اتحصّلت مع الفاتورة.', lbl, a.invoice_no;
        END IF;
        IF a.total_amount IS NULL THEN
            RAISE EXCEPTION '%: فاتورة البيع % مافيهاش مبلغ بعملة % — كل فاتورة بتتحصّل بعملتها.', lbl, a.invoice_no,
                p.currency_code;
        END IF;
        IF a.paid_amount > a.total_amount - a.returned_amount THEN
            RAISE EXCEPTION '%: المحصّل على فاتورة البيع % (% %) أكتر من المستحق عليها (% بعد المرتجع) — ممكن تكون اتحصّلت من جهاز تاني؛ دوس «تحديث».',
                lbl, a.invoice_no, a.paid_amount, p.currency_code, a.total_amount - a.returned_amount;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER customer_receipts_alloc_trg AFTER INSERT OR UPDATE ON cash.customer_receipts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.receipt_allocations_check();
CREATE CONSTRAINT TRIGGER receipt_allocations_check_trg AFTER INSERT OR UPDATE OR DELETE ON cash.receipt_allocations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.receipt_allocations_check();

-- (ب) المرحّل لازم ليه قيد مرحّل · المسودة مالهاش قيد. السند بعملة: الخزنة / البنك = المبلغ بالجنيه ·
--     الطرف = التوزيع بسعر الفواتير + الباقي بسعر السند (والفرق = فروق العملة).
--     سند الصرف: دائن الخزنة / مدين المورد · سند القبض: مدين الخزنة / دائن العميل.
CREATE OR REPLACE FUNCTION cash.vouchers_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k        record := cash.voucher_kind(TG_TABLE_NAME);
    j        jsonb;
    e        record;
    lbl      text;
    src      integer;
    moved    numeric;
    party    numeric;
    expected numeric;
    receipt  boolean := TG_TABLE_NAME = 'customer_receipts';
BEGIN
    EXECUTE format('SELECT to_jsonb(t) FROM %I.%I t WHERE id = $1', TG_TABLE_SCHEMA, TG_TABLE_NAME) INTO j USING NEW.id;
    IF j IS NULL THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    lbl := k.label || ' ' || coalesce(j ->> 'voucher_no', 'مسودة ' || (j ->> 'draft_no'));
    SELECT id, status, total_debit INTO e FROM acc.journal_entries
     WHERE source_type = k.doc_type AND source_id = (j ->> 'id')::integer;
    IF j ->> 'status' = 'posted' THEN
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION '% مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', lbl;
        END IF;
        IF TG_TABLE_NAME NOT IN ('supplier_payments', 'customer_receipts') THEN
            IF e.total_debit <> (j ->> 'amount')::numeric THEN
                RAISE EXCEPTION 'قيد % مبلغه % مش زي مبلغ السند %.', lbl, e.total_debit, j ->> 'amount';
            END IF;
            RETURN NULL;
        END IF;
        IF j ->> 'treasury_id' IS NOT NULL THEN
            SELECT account_id INTO src FROM cash.treasuries WHERE id = (j ->> 'treasury_id')::integer;
        ELSE
            SELECT account_id INTO src FROM cash.bank_accounts WHERE id = (j ->> 'bank_account_id')::integer;
        END IF;
        IF receipt THEN
            SELECT coalesce(sum(debit - credit) FILTER (WHERE account_id = src), 0),
                   coalesce(sum(credit - debit) FILTER (WHERE customer_id = (j ->> 'customer_id')::integer), 0)
              INTO moved, party FROM acc.journal_lines WHERE entry_id = e.id;
            SELECT coalesce(sum(amount_base), 0)
                   + round(((j ->> 'amount')::numeric - coalesce(sum(amount), 0)) * (j ->> 'fx_rate')::numeric, 2)
              INTO expected FROM cash.receipt_allocations WHERE customer_receipt_id = (j ->> 'id')::integer;
        ELSE
            SELECT coalesce(sum(credit - debit) FILTER (WHERE account_id = src), 0),
                   coalesce(sum(debit - credit) FILTER (WHERE supplier_id = (j ->> 'supplier_id')::integer), 0)
              INTO moved, party FROM acc.journal_lines WHERE entry_id = e.id;
            SELECT coalesce(sum(amount_base), 0)
                   + round(((j ->> 'amount')::numeric - coalesce(sum(amount), 0)) * (j ->> 'fx_rate')::numeric, 2)
              INTO expected FROM cash.voucher_allocations WHERE supplier_payment_id = (j ->> 'id')::integer;
        END IF;
        IF moved <> (j ->> 'amount_base')::numeric THEN
            RAISE EXCEPTION 'قيد % % الخزنة / البنك بـ % مش زي مبلغ السند بالجنيه %.', lbl,
                CASE WHEN receipt THEN 'داخل' ELSE 'طالع من' END, moved, j ->> 'amount_base';
        END IF;
        IF party <> expected THEN
            RAISE EXCEPTION 'قيد % قافل من حساب % % والمفروض % (التوزيع بسعر الفواتير + الباقي بسعر السند).',
                lbl, CASE WHEN receipt THEN 'العميل' ELSE 'المورد' END, party, expected;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION '% مسودة وليه قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.', lbl;
    END IF;
    RETURN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 6) فاتورة البيع مايتلغيش ترحيلها ولا تتمسح وعليها تحصيل مرحّل
-- ---------------------------------------------------------------------------
CREATE FUNCTION sales.sales_invoices_receipts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    IF OLD.status = 'posted' AND (TG_OP = 'DELETE' OR NEW.status <> 'posted') THEN
        SELECT cr.voucher_no INTO r
          FROM cash.receipt_allocations a JOIN cash.customer_receipts cr ON cr.id = a.customer_receipt_id
         WHERE a.sales_invoice_id = OLD.id AND cr.status = 'posted' ORDER BY cr.id LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'فاتورة البيع % عليها تحصيل مرحّل (سند القبض %) — الغي ترحيل السند أو شيل الفاتورة من توزيعه الأول.',
                OLD.invoice_no, r.voucher_no;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER sales_invoices_receipts_trg BEFORE UPDATE OR DELETE ON sales.sales_invoices
    FOR EACH ROW EXECUTE FUNCTION sales.sales_invoices_receipts_guard();

-- ---------------------------------------------------------------------------
-- 7) قواعد الترحيل: فروق العملة مع سند القبض
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('customer_receipt', 'fx_loss', 'فروق عملة مدينة', 'dr', 'default', 'fx_loss', NULL, NULL, 4),
    ('customer_receipt', 'fx_gain', 'فروق عملة دائنة', 'cr', 'default', 'fx_gain', NULL, NULL, 5);
