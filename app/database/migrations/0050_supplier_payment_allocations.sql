-- 0050: «سداد الموردين» (المرحلة 7) — تحديث «سند الصرف»: **عملة للسند** + **ربطه بفواتير الشراء** + **فروق العملة**.
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (2026-11-28 — بعد نماذج mockups/phase7/04_supplier_payments.html):
--   * مفيش شاشة جديدة: «سند الصرف» هو «سندات الموردين» وبيتحدّث هو.
--   * **السند بعملة** (بتتختار الأول) و«صرف من» = **خزنة أو حساب بنك بنفس العملة بس** (البنك زي الخزنة بالظبط).
--   * اختيار الفواتير من نافذة «📋 الفواتير» **بالتوتال** (الإجمالي − المرتجع المربوط − المسدد = المتبقي).
--   * **كل فاتورة بتتسدد بعملتها بس** — والفاتورة «متعددة العملات» = جزء لكل عملة، وكل جزء بيتسدد بسند بعملته.
--   * «وزّع الأقدم الأول» (بالاستحقاق) + تعديل · اللي مااتوزّعش = **دفعة مقدمة على حساب المورد بنفس العملة**.
--   * **فرق العملة بيتقيّد مع السند**: المورد بيتقفل بسعر الفاتورة · الخزنة / البنك بسعر السند · الفرق على
--     «فروق عملة مدينة / دائنة» (اتقدّم من المرحلة 8).
-- اللي في السكريبت:
--   1) `cash.supplier_payments`: `currency_code` + `fx_rate` + `amount_base` (بالجنيه = المبلغ × السعر).
--   2) `cash.voucher_allocations`: السند · الفاتورة · المبلغ بالعملة · سعر الفاتورة · المبلغ بالجنيه بسعر الفاتورة.
--   3) `purch.purchase_invoice_dues`: المستحق على كل (فاتورة آجلة مرحّلة + عملة).
--   4) الحماية في القاعدة: عملة الخزنة / البنك = عملة السند · الفاتورة لنفس المورد · الموزّع ≤ مبلغ السند ·
--      **المسدد على الفاتورة ≤ المتبقي بقفل الفاتورة** (جهازين بيدفعوا نفس الفاتورة ← التاني يترفض) ·
--      قيد السند: دائن الخزنة = المبلغ بالجنيه · مدين المورد = التوزيع بسعر الفواتير + الباقي بسعر السند ·
--      فاتورة الشراء مايتلغيش ترحيلها وعليها سداد مرحّل.
-- المسودة **مابتخصمش** من المتبقي على الفاتورة — المرحّل بس (ملف 03، الفكرة 21).

-- ---------------------------------------------------------------------------
-- 1) عملة السند
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    base text := (SELECT code FROM core.currencies WHERE is_base);
BEGIN
    -- الافتراضي الثابت بيملا السندات القديمة من غير ما يشغّل تريجر الحماية (المرحّل مايتعدّلش).
    EXECUTE format('ALTER TABLE cash.supplier_payments '
                   'ADD COLUMN currency_code char(3) NOT NULL DEFAULT %L '
                   '    REFERENCES core.currencies (code) ON DELETE RESTRICT, '
                   'ADD COLUMN fx_rate numeric(18,6) NOT NULL DEFAULT 1, '
                   'ADD COLUMN amount_base numeric(18,2)', base);
END $$;
ALTER TABLE cash.supplier_payments ALTER COLUMN currency_code DROP DEFAULT;
ALTER TABLE cash.supplier_payments DISABLE TRIGGER USER;
UPDATE cash.supplier_payments SET amount_base = amount;
ALTER TABLE cash.supplier_payments ENABLE TRIGGER USER;
ALTER TABLE cash.supplier_payments
    ALTER COLUMN amount_base SET NOT NULL,
    ADD CONSTRAINT supplier_payments_rate_chk CHECK (fx_rate > 0),
    ADD CONSTRAINT supplier_payments_base_chk CHECK (amount_base = round(amount * fx_rate, 2));
CREATE INDEX supplier_payments_currency_idx ON cash.supplier_payments (currency_code);
COMMENT ON COLUMN cash.supplier_payments.currency_code IS 'عملة السند = عملة الخزنة / البنك = عملة الفواتير اللي بيسددها';
COMMENT ON COLUMN cash.supplier_payments.fx_rate IS 'سعر الصرف بتاريخ السند (core.rate_on ويتعدّل) — الأساسية = 1';
COMMENT ON COLUMN cash.supplier_payments.amount_base IS 'المبلغ بالجنيه = المبلغ × السعر (اللي خرج من الخزنة / البنك)';
COMMENT ON TABLE cash.supplier_payments IS
    'سند صرف: مورد + عملة + خزنة أو بنك بنفس العملة + المبلغ · بيتوزّع على فواتير الشراء (cash.voucher_allocations) · '
    'مسودة ← مرحّل بقيد أوتوماتيك (+ فروق العملة) · الرقم من أول ترحيل';

-- ---------------------------------------------------------------------------
-- 2) الحماية: العملة + عملة الخزنة / البنك (دالة واحدة لكل جداول السندات — القبض لسه بالعملة الأساسية)
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
    has_cur  boolean := TG_TABLE_NAME = 'supplier_payments';
    cur_changed boolean := false;
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

    -- العملة (سند الصرف): نشطة · الأساسية سعرها 1.
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
        IF cur_changed AND EXISTS (SELECT 1 FROM cash.voucher_allocations WHERE supplier_payment_id = NEW.id) THEN
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
           AND EXISTS (SELECT 1 FROM cash.voucher_allocations WHERE supplier_payment_id = NEW.id) THEN
            RAISE EXCEPTION 'السند موزّع على فواتير المورد القديم — شيل التوزيع الأول عشان تغيّر المورد.';
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

-- ---------------------------------------------------------------------------
-- 3) جدول التوزيع على الفواتير
-- ---------------------------------------------------------------------------
CREATE TABLE cash.voucher_allocations (
    id                   bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_payment_id  integer        NOT NULL REFERENCES cash.supplier_payments (id) ON DELETE CASCADE,
    purchase_invoice_id  integer        NOT NULL REFERENCES purch.purchase_invoices (id) ON DELETE RESTRICT,
    currency_code        char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,   -- = عملة السند
    amount               numeric(18,2)  NOT NULL,                 -- بعملة السند (= عملة جزء الفاتورة)
    invoice_rate         numeric(18,6)  NOT NULL DEFAULT 1,       -- سعر الفاتورة لجزء العملة ده (بالجنيه ÷ بالعملة)
    amount_base          numeric(18,2)  NOT NULL,                 -- بالجنيه بسعر الفاتورة = اللي اتقفل من حساب المورد
    created_by           integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at           timestamptz    NOT NULL DEFAULT now(),
    CONSTRAINT voucher_allocations_unique UNIQUE (supplier_payment_id, purchase_invoice_id),
    CONSTRAINT voucher_allocations_amount_chk CHECK (amount > 0 AND amount_base >= 0 AND invoice_rate > 0)
);
COMMENT ON TABLE cash.voucher_allocations IS
    'توزيع سند الصرف على فواتير الشراء: المبلغ بعملة السند + بالجنيه بسعر الفاتورة (منه فرق العملة وأعمار الديون) — '
    'اللي مااتوزّعش من السند = دفعة مقدمة على حساب المورد. (سند القبض ↔ فواتير البيع هيتضاف في المرحلة 8)';
CREATE INDEX voucher_allocations_invoice_idx ON cash.voucher_allocations (purchase_invoice_id);
CREATE INDEX voucher_allocations_currency_idx ON cash.voucher_allocations (currency_code);
CREATE INDEX voucher_allocations_created_by_idx ON cash.voucher_allocations (created_by);

-- السطر: السند مسودة (المرحّل توزيعه مايتغيّرش) · الفاتورة لنفس المورد · العملة = عملة السند.
CREATE FUNCTION cash.voucher_allocations_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p    record;
    i    record;
    lbl  text;
    pid  integer := CASE WHEN TG_OP = 'DELETE' THEN OLD.supplier_payment_id ELSE NEW.supplier_payment_id END;
BEGIN
    SELECT v.id, v.status, v.voucher_no, v.draft_no, v.supplier_id, v.currency_code, y.status AS year_status,
           y.year_code INTO p
      FROM cash.supplier_payments v JOIN core.fiscal_years y ON y.id = v.fiscal_year_id WHERE v.id = pid;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;     -- السند اتمسح (CASCADE)
    END IF;
    lbl := 'سند الصرف ' || coalesce(p.voucher_no, 'مسودة ' || p.draft_no);
    IF p.year_status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — توزيع سنداتها مايتغيّرش.', p.year_code;
    END IF;
    IF p.status = 'posted' THEN
        RAISE EXCEPTION '% مرحّل — توزيعه مايتغيّرش (الغي ترحيله الأول).', lbl;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.supplier_payment_id <> OLD.supplier_payment_id THEN
        RAISE EXCEPTION 'سطر التوزيع مايتنقلش لسند تاني.';
    END IF;
    SELECT invoice_no, supplier_id INTO i FROM purch.purchase_invoices WHERE id = NEW.purchase_invoice_id;
    IF i.supplier_id IS DISTINCT FROM p.supplier_id THEN
        RAISE EXCEPTION 'فاتورة الشراء % لمورد تاني — السند بيتوزّع على فواتير مورده بس.', i.invoice_no;
    END IF;
    NEW.currency_code := p.currency_code;
    RETURN NEW;
END $$;
CREATE TRIGGER voucher_allocations_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.voucher_allocations
    FOR EACH ROW EXECUTE FUNCTION cash.voucher_allocations_guard();

-- ---------------------------------------------------------------------------
-- 4) المستحق على كل (فاتورة آجلة مرحّلة + عملة): الإجمالي − المرتجع الآجل المربوط − السداد المرحّل
-- ---------------------------------------------------------------------------
CREATE VIEW purch.purchase_invoice_dues AS
SELECT i.id AS invoice_id, i.invoice_no, i.invoice_date, i.due_date, i.supplier_id, i.supplier_invoice_no,
       i.stock_kind, i.currency_mode, i.project_id,
       t.currency_code, t.total_amount, t.total_base,
       coalesce(r.returned_amount, 0) AS returned_amount, coalesce(r.returned_base, 0) AS returned_base,
       coalesce(p.paid_amount, 0) AS paid_amount, coalesce(p.paid_base, 0) AS paid_base,
       t.total_amount - coalesce(r.returned_amount, 0) - coalesce(p.paid_amount, 0) AS remaining_amount,
       t.total_base - coalesce(r.returned_base, 0) - coalesce(p.paid_base, 0) AS remaining_base
  FROM purch.purchase_invoices i
  JOIN LATERAL (SELECT l.currency_code, sum(l.total_amount) AS total_amount, sum(l.total_base) AS total_base
                  FROM purch.purchase_invoice_lines l WHERE l.invoice_id = i.id GROUP BY l.currency_code) t ON true
  LEFT JOIN LATERAL (SELECT sum(rl.total_amount) AS returned_amount, sum(rl.total_base) AS returned_base
                       FROM purch.purchase_invoice_lines pl
                       JOIN purch.purchase_return_lines rl ON rl.source_line_id = pl.id
                       JOIN purch.purchase_returns rt ON rt.id = rl.return_id
                      WHERE pl.invoice_id = i.id AND rl.currency_code = t.currency_code
                        AND rt.status = 'posted' AND rt.pay_kind = 'credit') r ON true
  LEFT JOIN LATERAL (SELECT sum(a.amount) AS paid_amount, sum(a.amount_base) AS paid_base
                       FROM cash.voucher_allocations a
                       JOIN cash.supplier_payments sp ON sp.id = a.supplier_payment_id
                      WHERE a.purchase_invoice_id = i.id AND a.currency_code = t.currency_code
                        AND sp.status = 'posted') p ON true
 WHERE i.status = 'posted' AND i.pay_kind = 'credit';
COMMENT ON VIEW purch.purchase_invoice_dues IS
    'المستحق على فواتير الشراء الآجلة المرحّلة لكل عملة: الإجمالي − المرتجع الآجل المربوط − السداد المرحّل = المتبقي '
    '(بالعملة وبالجنيه بسعر الفاتورة) — المسودات مابتخصمش';

-- ---------------------------------------------------------------------------
-- 5) آخر الـ transaction: التوزيع صح + القيد مطابق
-- ---------------------------------------------------------------------------
-- (أ) الموزّع ≤ مبلغ السند · والمرحّل: الفاتورة مرحّلة آجلة ولنفس المورد وفيها العملة · المسدد ≤ المتبقي (بقفل الفاتورة).
CREATE FUNCTION cash.payment_allocations_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pid  integer;
    p    record;
    a    record;
    tot  numeric;
    lbl  text;
BEGIN
    IF TG_TABLE_NAME = 'supplier_payments' THEN
        pid := NEW.id;
    ELSE
        pid := CASE WHEN TG_OP = 'DELETE' THEN OLD.supplier_payment_id ELSE NEW.supplier_payment_id END;
    END IF;
    SELECT id, status, voucher_no, draft_no, supplier_id, currency_code, amount INTO p
      FROM cash.supplier_payments WHERE id = pid;
    IF NOT FOUND THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    lbl := 'سند الصرف ' || coalesce(p.voucher_no, 'مسودة ' || p.draft_no);
    SELECT coalesce(sum(amount), 0) INTO tot FROM cash.voucher_allocations WHERE supplier_payment_id = pid;
    IF tot > p.amount THEN
        RAISE EXCEPTION '%: الموزّع على الفواتير (%) أكتر من مبلغ السند (%).', lbl, tot, p.amount;
    END IF;
    IF p.status <> 'posted' OR tot = 0 THEN
        RETURN NULL;
    END IF;
    -- قفل الفواتير بترتيب ثابت: جهازين بيدفعوا نفس الفاتورة ← التاني بيستنى وبعدين بيشوف سداد الأول.
    PERFORM 1 FROM purch.purchase_invoices
      WHERE id IN (SELECT purchase_invoice_id FROM cash.voucher_allocations WHERE supplier_payment_id = pid)
      ORDER BY id FOR UPDATE;
    FOR a IN
        SELECT al.amount, al.currency_code, i.invoice_no, i.draft_no, i.status, i.pay_kind, i.supplier_id,
               d.total_amount, d.returned_amount, d.paid_amount
          FROM cash.voucher_allocations al
          JOIN purch.purchase_invoices i ON i.id = al.purchase_invoice_id
          LEFT JOIN purch.purchase_invoice_dues d ON d.invoice_id = al.purchase_invoice_id
                                                  AND d.currency_code = al.currency_code
         WHERE al.supplier_payment_id = pid ORDER BY i.id
    LOOP
        IF a.currency_code <> p.currency_code THEN
            RAISE EXCEPTION '%: سطر التوزيع على % بعملة % والسند بعملة %.', lbl, a.invoice_no, a.currency_code,
                p.currency_code;
        END IF;
        IF a.supplier_id <> p.supplier_id THEN
            RAISE EXCEPTION '%: فاتورة الشراء % لمورد تاني.', lbl, a.invoice_no;
        END IF;
        IF a.status <> 'posted' THEN
            RAISE EXCEPTION '%: فاتورة الشراء % مش مرحّلة — مايتسددش عليها.', lbl,
                coalesce(a.invoice_no, 'مسودة ' || a.draft_no);
        END IF;
        IF a.pay_kind <> 'credit' THEN
            RAISE EXCEPTION '%: فاتورة الشراء % نقدية — اتسددت مع الفاتورة.', lbl, a.invoice_no;
        END IF;
        IF a.total_amount IS NULL THEN
            RAISE EXCEPTION '%: فاتورة الشراء % مافيهاش مبلغ بعملة % — كل فاتورة بتتسدد بعملتها.', lbl, a.invoice_no,
                p.currency_code;
        END IF;
        IF a.paid_amount > a.total_amount - a.returned_amount THEN
            RAISE EXCEPTION '%: المسدد على فاتورة الشراء % (% %) أكتر من المستحق عليها (% بعد المرتجع) — ممكن تكون اتسددت من جهاز تاني؛ دوس «تحديث».',
                lbl, a.invoice_no, a.paid_amount, p.currency_code, a.total_amount - a.returned_amount;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER supplier_payments_alloc_trg AFTER INSERT OR UPDATE ON cash.supplier_payments
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.payment_allocations_check();
CREATE CONSTRAINT TRIGGER voucher_allocations_check_trg AFTER INSERT OR UPDATE OR DELETE ON cash.voucher_allocations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.payment_allocations_check();

-- (ب) المرحّل لازم ليه قيد مرحّل · المسودة مالهاش قيد. سند الصرف: دائن الخزنة / البنك = المبلغ بالجنيه ·
--     مدين المورد = التوزيع بسعر الفواتير + الباقي بسعر السند (والفرق = فروق العملة).
CREATE OR REPLACE FUNCTION cash.vouchers_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    k        record := cash.voucher_kind(TG_TABLE_NAME);
    j        jsonb;
    e        record;
    lbl      text;
    src      integer;
    paid     numeric;
    party    numeric;
    expected numeric;
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
        IF TG_TABLE_NAME <> 'supplier_payments' THEN
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
        SELECT coalesce(sum(credit - debit) FILTER (WHERE account_id = src), 0),
               coalesce(sum(debit - credit) FILTER (WHERE supplier_id = (j ->> 'supplier_id')::integer), 0)
          INTO paid, party FROM acc.journal_lines WHERE entry_id = e.id;
        IF paid <> (j ->> 'amount_base')::numeric THEN
            RAISE EXCEPTION 'قيد % طالع من الخزنة / البنك بـ % مش زي مبلغ السند بالجنيه %.', lbl, paid, j ->> 'amount_base';
        END IF;
        SELECT coalesce(sum(amount_base), 0)
               + round(((j ->> 'amount')::numeric - coalesce(sum(amount), 0)) * (j ->> 'fx_rate')::numeric, 2)
          INTO expected FROM cash.voucher_allocations WHERE supplier_payment_id = (j ->> 'id')::integer;
        IF party <> expected THEN
            RAISE EXCEPTION 'قيد % قافل من حساب المورد % والمفروض % (التوزيع بسعر الفواتير + الباقي بسعر السند).',
                lbl, party, expected;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION '% مسودة وليه قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.', lbl;
    END IF;
    RETURN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 6) فاتورة الشراء مايتلغيش ترحيلها وعليها سداد مرحّل
-- ---------------------------------------------------------------------------
CREATE FUNCTION purch.purchase_invoices_payments_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    IF OLD.status = 'posted' AND (TG_OP = 'DELETE' OR NEW.status <> 'posted') THEN
        SELECT sp.voucher_no INTO r
          FROM cash.voucher_allocations a JOIN cash.supplier_payments sp ON sp.id = a.supplier_payment_id
         WHERE a.purchase_invoice_id = OLD.id AND sp.status = 'posted' ORDER BY sp.id LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'فاتورة الشراء % عليها سداد مرحّل (سند الصرف %) — الغي ترحيل السند أو شيل الفاتورة من توزيعه الأول.',
                OLD.invoice_no, r.voucher_no;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER purchase_invoices_payments_trg BEFORE UPDATE OR DELETE ON purch.purchase_invoices
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_invoices_payments_guard();

-- ---------------------------------------------------------------------------
-- 7) قواعد الترحيل: فروق العملة مع سند الصرف
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('supplier_payment', 'fx_loss', 'فروق عملة مدينة', 'dr', 'default', 'fx_loss', NULL, NULL, 4),
    ('supplier_payment', 'fx_gain', 'فروق عملة دائنة', 'cr', 'default', 'fx_gain', NULL, NULL, 5);
