-- 0051: «فاتورة البيع / إذن الصرف» (المرحلة 8 — أول بند · النموذج 1 من mockups/phase8/01_sales_invoice.html،
--       اختيار المستخدم 2026-11-28) — «زي فاتورة الشراء وعكسها».
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- ردود المستخدم (2026-11-28) + الباقي بالاقتراح اللي في ملف النماذج:
--   * **زي فاتورة الشراء (0048) وعكسها** — نفس العملات: «عملة واحدة» (الافتراضي عملة العميل بسعرها بتاريخ الفاتورة
--     ويتعدّل) أو **«متعدد العملات»** (كل سطر بعملته وسعره، من غير خصم فاتورة). دين العميل بيتسجل **سطر مدين لكل
--     (عملة + سعر)** بالجنيه ومعاه العملة والسعر والمبلغ بالعملة.
--   * السعر الافتراضي = «سعر البيع» من الصنف × معامل الوحدة ÷ سعر الصرف (الـ Service) ويتعدّل · البيع بأقل من التكلفة
--     تنبيه بس.
--   * **قيد واحد مزدوج** بتاريخ الفاتورة (قواعد sales_invoice من 0032):
--       مدين العميل (حساب رقابة مجموعته + بُعد العميل) بالإجمالي · مدين «خصم مسموح به» (5303) بالخصم قبل الضريبة ·
--       دائن المبيعات (من نوع الصنف — الخدمي على «إيراد الخدمات» 4102 لو نوعه مالوش حساب مبيعات) بالإجمالي قبل
--       الخصم · دائن ض.ق.م مخرجات (من كود الضريبة) ·
--       **مدين تكلفة المبيعات / دائن المخزون بالمتوسط المرجّح** (من دفتر المخزون وقت الترحيل — الخدمي مالوش تكلفة).
--   * **البيع أكتر من المتاح ممنوع** (في المخزن + فاتورة / بيان) إلا بصلاحية «من غير رصيد» — والمراجعة الحقيقية جوه
--     قفل الصنف وقت الترحيل (دفتر المخزون 0043).
--   * **حد الائتمان** (العميل): (رصيد العميل بالجنيه + الفاتورة الآجلة) > الحد ← ممنوع إلا بصلاحية جديدة «تجاوز حد
--     الائتمان» (الـ Service جوه قفل صف العميل) · الحد صفر = مفيش حد · النقدي مابيتحسبش.
--   * «نقدي» = **قيد قبض لوحده** (نوعه «قبض فاتورة بيع نقدية»: مدين الخزنة أو البنك / دائن العميل) بيترحّل ويتلغي مع
--     الفاتورة · **«عميل نقدي» (النظامي — 0019) نقدي بس** · مفيش «مدفوع جزء» (الجزء بسند قبض بالتوزيع بعدين).
--   * أمر التوريد اختياري (الجاري — ولو ليه عميل لازم نفس عميل الفاتورة) والإيراد والتكلفة ببُعده · الاستحقاق = التاريخ
--     + مدة سداد العميل ويتعدّل · خصم % على السطر + خصم على الفاتورة بيتوزّع على السطور بالقيمة · فاتورة / بيان (البيان
--     من غير ض.ق.م وبيطلع من رصيد «بيان») · الرقم INV-2026-00001 مع أول ترحيل · **الرقم الإلكتروني اختياري ويتكتب حتى
--     بعد الترحيل** · مسودة ← ترحيل ← إلغاء ترحيل · المخزن الوهمي ممنوع · الموظف المسؤول اختياري · مفيش نقل من الأكسس.
-- الحماية في القاعدة نفسها: زي فاتورة الشراء (السنة · المرحّل · الرقم · العميل · الخزنة · المخزن · الصنف · الوحدة ·
-- الضريبة · العملة) + العميل النقدي نقدي بس + أمر التوريد لنفس العميل + الموظف نشط + المرحّلة ليها حركة مخزون لكل
-- صنف مخزني وقيد مرحّل بالإجمالي + الخصم + التكلفة (+ القبض لو نقدي) والمسودة مالهاش.

-- ---------------------------------------------------------------------------
-- 1) قواعد الترحيل: قيد القبض النقدي (قواعد sales_invoice نفسها موجودة من 0032)
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('sales_receipt', 'قبض فاتورة بيع نقدية', (SELECT max(sort_order) + 1 FROM acc.posting_doc_types));
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('sales_receipt', 'cash',       'الخزينة', 'dr', 'treasury', NULL, NULL, NULL,       1),
    ('sales_receipt', 'bank',       'البنك',   'dr', 'bank',     NULL, NULL, NULL,       2),
    ('sales_receipt', 'receivable', 'العملاء', 'cr', 'party',    NULL, NULL, 'customer', 3);

-- ---------------------------------------------------------------------------
-- 2) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE sales.sales_invoices_draft_seq AS integer;
CREATE TABLE sales.sales_invoices (
    id                   integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_no           varchar(30),                                  -- INV-2026-00001 (من أول ترحيل)
    draft_no             integer        NOT NULL DEFAULT nextval('sales.sales_invoices_draft_seq'),
    invoice_date         date           NOT NULL,
    fiscal_year_id       integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    customer_id          integer        NOT NULL REFERENCES party.customers (id) ON DELETE RESTRICT,
    customer_ref         varchar(40),                                  -- رقم أمر الشراء عند العميل (اختياري)
    e_invoice_no         varchar(60),                                  -- الرقم الإلكتروني (اختياري — يتكتب بعد الترحيل)
    stock_kind           varchar(10)    NOT NULL DEFAULT 'invoice',     -- invoice فاتورة · statement بيان
    prices_include_vat   boolean        NOT NULL DEFAULT false,
    pay_kind             varchar(10)    NOT NULL DEFAULT 'credit',      -- credit آجل · cash نقدي
    due_date             date,
    source_kind          varchar(10),                                  -- نقدي: treasury خزنة · bank بنك
    treasury_id          integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id      integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    warehouse_id         integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- المخزن الافتراضي للسطور
    project_id           integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,     -- أمر التوريد (اختياري)
    employee_id          integer        REFERENCES party.employees (id) ON DELETE RESTRICT,  -- الموظف المسؤول (اختياري)
    currency_mode        varchar(10)    NOT NULL DEFAULT 'single',      -- single عملة واحدة · multi متعدد العملات
    currency_code        char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    fx_rate              numeric(18,6)  NOT NULL DEFAULT 1,
    discount_amount      numeric(18,2)  NOT NULL DEFAULT 0,             -- خصم الفاتورة بعملتها («عملة واحدة» بس)
    -- الإجماليات (بيحسبها الـ Service مع كل حفظ — بعملة الفاتورة، و«متعدد» = بالجنيه):
    gross_amount         numeric(18,2)  NOT NULL DEFAULT 0,
    line_discount        numeric(18,2)  NOT NULL DEFAULT 0,
    net_amount           numeric(18,2)  NOT NULL DEFAULT 0,             -- قبل الضريبة بعد كل الخصومات
    vat_amount           numeric(18,2)  NOT NULL DEFAULT 0,
    total_amount         numeric(18,2)  NOT NULL DEFAULT 0,
    net_base             numeric(18,2)  NOT NULL DEFAULT 0,             -- بالجنيه
    vat_base             numeric(18,2)  NOT NULL DEFAULT 0,
    total_base           numeric(18,2)  NOT NULL DEFAULT 0,
    discount_base        numeric(18,2)  NOT NULL DEFAULT 0,             -- كل الخصومات قبل الضريبة بالجنيه (5303)
    description          varchar(500),
    status               varchar(10)    NOT NULL DEFAULT 'draft',
    created_by           integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at            timestamptz,
    posted_by            integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at           timestamptz    NOT NULL DEFAULT now(),
    updated_at           timestamptz    NOT NULL DEFAULT now(),
    row_version          integer        NOT NULL DEFAULT 1 CONSTRAINT sales_invoices_row_version_chk CHECK (row_version > 0),
    CONSTRAINT sales_invoices_no_unique UNIQUE (invoice_no),
    CONSTRAINT sales_invoices_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT sales_invoices_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT sales_invoices_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT sales_invoices_pay_chk CHECK (
        (pay_kind = 'credit' AND due_date IS NOT NULL AND due_date >= invoice_date AND source_kind IS NULL
         AND treasury_id IS NULL AND bank_account_id IS NULL)
        OR (pay_kind = 'cash' AND due_date IS NULL AND (
            (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
            OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)))),
    CONSTRAINT sales_invoices_mode_chk CHECK (currency_mode IN ('single', 'multi')
        AND (currency_mode = 'single' OR (fx_rate = 1 AND discount_amount = 0))),
    CONSTRAINT sales_invoices_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT sales_invoices_amounts_chk CHECK (
        discount_amount >= 0 AND gross_amount >= 0 AND line_discount >= 0 AND net_amount >= 0 AND vat_amount >= 0
        AND total_amount = net_amount + vat_amount AND net_base >= 0 AND vat_base >= 0 AND discount_base >= 0
        AND total_base = net_base + vat_base),
    CONSTRAINT sales_invoices_ref_chk CHECK (customer_ref IS NULL OR btrim(customer_ref) <> ''),
    CONSTRAINT sales_invoices_einv_chk CHECK (e_invoice_no IS NULL OR btrim(e_invoice_no) <> ''),
    CONSTRAINT sales_invoices_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT sales_invoices_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR invoice_no IS NOT NULL))
);
ALTER SEQUENCE sales.sales_invoices_draft_seq OWNED BY sales.sales_invoices.draft_no;
COMMENT ON TABLE sales.sales_invoices IS
    'فاتورة البيع (إذن الصرف): عميل + أصناف بمخازنها · مسودة ← مرحّلة بحركات دفتر المخزون (بالمتوسط) وقيد واحد '
    '(وقيد قبض لو نقدي) · عملة واحدة أو متعدد';

CREATE TABLE sales.sales_invoice_lines (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id       integer        NOT NULL REFERENCES sales.sales_invoices (id) ON DELETE CASCADE,
    line_no          smallint       NOT NULL,
    item_id          integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    warehouse_id     integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- الخدمي مالوش مخزن
    unit_id          integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,
    factor           numeric(18,6)  NOT NULL DEFAULT 1,
    qty              numeric(18,6)  NOT NULL,
    base_qty         numeric(18,6)  GENERATED ALWAYS AS (round(qty * factor, 6)) STORED,
    unit_price       numeric(18,6)  NOT NULL,                           -- سعر الوحدة المختارة بعملة السطر
    discount_pct     numeric(7,4)   NOT NULL DEFAULT 0,
    tax_code_id      integer        REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,
    tax_rate         numeric(7,4)   NOT NULL DEFAULT 0,
    currency_code    char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    fx_rate          numeric(18,6)  NOT NULL DEFAULT 1,
    gross_amount     numeric(18,2)  NOT NULL,
    line_discount    numeric(18,2)  NOT NULL DEFAULT 0,
    header_discount  numeric(18,2)  NOT NULL DEFAULT 0,
    net_amount       numeric(18,2)  NOT NULL,
    vat_amount       numeric(18,2)  NOT NULL DEFAULT 0,
    total_amount     numeric(18,2)  NOT NULL,
    net_base         numeric(18,2)  NOT NULL,
    vat_base         numeric(18,2)  NOT NULL DEFAULT 0,
    total_base       numeric(18,2)  NOT NULL,
    discount_base    numeric(18,2)  NOT NULL DEFAULT 0,                 -- خصم السطر + نصيبه قبل الضريبة بالجنيه
    CONSTRAINT sales_invoice_lines_no_unique UNIQUE (invoice_id, line_no),
    CONSTRAINT sales_invoice_lines_line_chk CHECK (line_no > 0),
    CONSTRAINT sales_invoice_lines_factor_chk CHECK (factor > 0),
    CONSTRAINT sales_invoice_lines_qty_chk CHECK (qty > 0 AND round(qty * factor, 6) > 0),
    CONSTRAINT sales_invoice_lines_price_chk CHECK (unit_price >= 0),
    CONSTRAINT sales_invoice_lines_disc_chk CHECK (discount_pct >= 0 AND discount_pct <= 100),
    CONSTRAINT sales_invoice_lines_tax_chk CHECK (tax_rate >= 0 AND (tax_code_id IS NOT NULL OR tax_rate = 0)),
    CONSTRAINT sales_invoice_lines_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT sales_invoice_lines_amounts_chk CHECK (
        gross_amount >= 0 AND line_discount >= 0 AND header_discount >= 0 AND line_discount <= gross_amount
        AND net_amount >= 0 AND vat_amount >= 0 AND total_amount = net_amount + vat_amount
        AND net_base = round(net_amount * fx_rate, 2) AND vat_base = round(vat_amount * fx_rate, 2)
        AND total_base = net_base + vat_base AND discount_base >= 0)
);
COMMENT ON TABLE sales.sales_invoice_lines IS
    'أصناف فاتورة البيع: الكمية بالوحدة × المعامل · السعر بعملة السطر · المبالغ بالعملة وبالجنيه · '
    'التكلفة من دفتر المخزون (booked_value) وقت الترحيل';

CREATE INDEX sales_invoices_date_idx ON sales.sales_invoices (invoice_date DESC, id DESC);
CREATE INDEX sales_invoices_year_idx ON sales.sales_invoices (fiscal_year_id, status);
CREATE INDEX sales_invoices_customer_idx ON sales.sales_invoices (customer_id, invoice_date DESC);
CREATE INDEX sales_invoices_einv_idx ON sales.sales_invoices (e_invoice_no) WHERE e_invoice_no IS NOT NULL;
CREATE INDEX sales_invoices_ref_idx ON sales.sales_invoices (customer_ref) WHERE customer_ref IS NOT NULL;
CREATE INDEX sales_invoices_treasury_idx ON sales.sales_invoices (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX sales_invoices_bank_idx ON sales.sales_invoices (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX sales_invoices_warehouse_idx ON sales.sales_invoices (warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX sales_invoices_project_idx ON sales.sales_invoices (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX sales_invoices_employee_idx ON sales.sales_invoices (employee_id) WHERE employee_id IS NOT NULL;
CREATE INDEX sales_invoices_currency_idx ON sales.sales_invoices (currency_code);
CREATE INDEX sales_invoices_created_by_idx ON sales.sales_invoices (created_by);
CREATE INDEX sales_invoices_posted_by_idx ON sales.sales_invoices (posted_by);
CREATE INDEX sales_invoices_due_idx ON sales.sales_invoices (due_date) WHERE pay_kind = 'credit';
CREATE INDEX sales_invoices_desc_idx ON sales.sales_invoices (lower(description) varchar_pattern_ops)
    WHERE description IS NOT NULL;
CREATE INDEX sales_invoice_lines_item_idx ON sales.sales_invoice_lines (item_id);
CREATE INDEX sales_invoice_lines_warehouse_idx ON sales.sales_invoice_lines (warehouse_id);
CREATE INDEX sales_invoice_lines_unit_idx ON sales.sales_invoice_lines (unit_id);
CREATE INDEX sales_invoice_lines_tax_idx ON sales.sales_invoice_lines (tax_code_id);
CREATE INDEX sales_invoice_lines_currency_idx ON sales.sales_invoice_lines (currency_code);

-- ---------------------------------------------------------------------------
-- 3) الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION sales.sales_invoices_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
    m        record;
    base     text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'فاتورة البيع ' || coalesce(OLD.invoice_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — فواتيرها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّلة — ماتتمسحش (الغي ترحيلها الأول).', lbl;
        END IF;
        IF OLD.invoice_no IS NOT NULL THEN
            RAISE EXCEPTION '% خدت رقم رسمي قبل كده — ماتتمسحش عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.invoice_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.invoice_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل فيها فاتورة.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.invoice_no IS NOT NULL THEN
            RAISE EXCEPTION 'الفاتورة الجديدة بتتسجل مسودة الأول (وبعدين تترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.invoice_no IS NOT NULL AND NEW.invoice_no IS DISTINCT FROM OLD.invoice_no) THEN
            RAISE EXCEPTION 'رقم الفاتورة ثابت.';
        END IF;
        IF OLD.invoice_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمها من سنة % — تاريخها لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                -- المرحّلة: الرقم الإلكتروني بس يتكتب / يتعدّل (بيتاخد من المنظومة بعد الترحيل).
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at' - 'e_invoice_no') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at' - 'e_invoice_no') THEN
                    RAISE EXCEPTION '% مرحّلة — ماتتعدّلش (الغي ترحيلها الأول) — الرقم الإلكتروني بس يتكتب.', lbl;
                END IF;
                NEW.updated_at := now();
                RETURN NEW;
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع الفاتورة مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NOT EXISTS (SELECT 1 FROM sales.sales_invoice_lines WHERE invoice_id = NEW.id) THEN
                RAISE EXCEPTION '% مافيهاش أصناف — ضيف صنف واحد على الأقل قبل الترحيل.', lbl;
            END IF;
            IF NEW.invoice_no IS NULL THEN
                NEW.invoice_no := core.next_document_no('sales_invoice', NEW.invoice_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- العميل: نشط · «العميل النقدي» (النظامي) نقدي بس.
    IF TG_OP = 'INSERT' OR posting OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
       OR NEW.pay_kind IS DISTINCT FROM OLD.pay_kind THEN
        SELECT code, name_ar, is_active, is_system INTO m FROM party.customers WHERE id = NEW.customer_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'العميل مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
        END IF;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'العميل «% %» موقوف — اختار عميل نشط.', m.code, m.name_ar;
        END IF;
        IF m.is_system AND NEW.pay_kind <> 'cash' THEN
            RAISE EXCEPTION '«%» للبيع الكاش — فاتورته نقدي بس (الآجل لعميل باسمه).', m.name_ar;
        END IF;
    END IF;

    -- العملة: «متعدد» = الفاتورة بالجنيه (كل سطر بعملته) · الأساسية سعرها 1 · العملة نشطة.
    SELECT code INTO base FROM core.currencies WHERE is_base;
    IF NEW.currency_mode = 'multi' AND NEW.currency_code <> base THEN
        RAISE EXCEPTION 'الفاتورة «متعددة العملات» إجمالياتها بالعملة الأساسية (%) — كل سطر بعملته.', base;
    END IF;
    IF NEW.currency_code = base AND NEW.fx_rate <> 1 THEN
        RAISE EXCEPTION 'العملة الأساسية (%) سعرها 1.', base;
    END IF;
    IF TG_OP = 'INSERT' OR posting OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
        IF NOT (SELECT is_active FROM core.currencies WHERE code = NEW.currency_code) THEN
            RAISE EXCEPTION 'العملة % موقوفة — اختار عملة نشطة.', NEW.currency_code;
        END IF;
    END IF;

    -- الخزنة / البنك (نقدي): نشط وبالعملة الأساسية (زي السندات).
    IF NEW.pay_kind = 'cash' AND (TG_OP = 'INSERT' OR posting OR NEW.treasury_id IS DISTINCT FROM OLD.treasury_id
                                  OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id) THEN
        IF NEW.treasury_id IS NOT NULL THEN
            SELECT code, name_ar, is_active, currency_code INTO m FROM cash.treasuries WHERE id = NEW.treasury_id FOR SHARE;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'الخزنة «% %» موقوفة — اختار خزنة نشطة.', m.code, m.name_ar;
            END IF;
        ELSE
            SELECT ba.code, b.name_ar, ba.is_active AND b.is_active AS is_active, ba.currency_code INTO m
              FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id
             WHERE ba.id = NEW.bank_account_id FOR SHARE OF ba;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'الحساب البنكي «% %» موقوف — اختار حساب نشط.', m.code, m.name_ar;
            END IF;
        END IF;
        IF m.currency_code IS DISTINCT FROM base THEN
            RAISE EXCEPTION '«% %» بعملة % — القبض النقدي في خزنة / بنك بالعملة الأساسية (%) بس.',
                m.code, m.name_ar, m.currency_code, base;
        END IF;
    END IF;

    -- أمر التوريد (اختياري): جاري · ولو ليه عميل لازم نفس عميل الفاتورة.
    IF NEW.project_id IS NOT NULL AND (TG_OP = 'INSERT' OR posting OR NEW.project_id IS DISTINCT FROM OLD.project_id
                                       OR NEW.customer_id IS DISTINCT FROM OLD.customer_id) THEN
        SELECT code, status, customer_id INTO m FROM acc.projects WHERE id = NEW.project_id FOR SHARE;
        IF m.status IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'أمر التوريد % مش جاري — اختار أمر جاري أو سيبه فاضي.', m.code;
        END IF;
        IF m.customer_id IS NOT NULL AND m.customer_id <> NEW.customer_id THEN
            RAISE EXCEPTION 'أمر التوريد % لعميل تاني — اختار أمر لنفس العميل أو سيبه فاضي.', m.code;
        END IF;
    END IF;

    -- الموظف المسؤول (اختياري): نشط وقت الإضافة أو التغيير.
    IF NEW.employee_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.employee_id IS DISTINCT FROM OLD.employee_id) THEN
        SELECT code, name_ar, is_active INTO m FROM party.employees WHERE id = NEW.employee_id FOR SHARE;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'الموظف «% %» موقوف — اختار موظف نشط أو سيبه فاضي.', m.code, m.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- السطور: الفاتورة لازم مسودة · الصنف نشط · المخزن (للمخزني) نشط ومش وهمي · الوحدة بمعاملها · الضريبة · العملة.
CREATE FUNCTION sales.sales_invoice_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h    record;
    it   record;
    w    record;
    tc   record;
    f    numeric;
    base text;
BEGIN
    SELECT status, invoice_no, draft_no, stock_kind, currency_mode, currency_code, fx_rate INTO h
      FROM sales.sales_invoices
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- الفاتورة نفسها اتمسحت (CASCADE)
    END IF;
    IF h.status <> 'draft' THEN
        RAISE EXCEPTION 'فاتورة البيع % مرحّلة — أصنافها ماتتعدّلش (الغي ترحيلها الأول).',
            coalesce(h.invoice_no, 'مسودة ' || h.draft_no);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.invoice_id <> OLD.invoice_id THEN
        RAISE EXCEPTION 'السطر مايتنقلش لفاتورة تانية.';
    END IF;

    SELECT code, name, is_service, is_active, unit_id INTO it FROM inv.items WHERE id = NEW.item_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الصنف مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT it.is_active AND (TG_OP = 'INSERT' OR NEW.item_id <> OLD.item_id) THEN
        RAISE EXCEPTION 'الصنف % «%» موقوف — مايتباعش.', it.code, it.name;
    END IF;
    IF it.is_service THEN
        IF NEW.warehouse_id IS NOT NULL THEN
            RAISE EXCEPTION 'الصنف % «%» خدمي — مالوش مخزن.', it.code, it.name;
        END IF;
    ELSIF NEW.warehouse_id IS NULL THEN
        RAISE EXCEPTION 'الصنف % «%»: اختار المخزن.', it.code, it.name;
    ELSIF TG_OP = 'INSERT' OR NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id THEN
        SELECT code, name_ar, is_active, is_virtual INTO w FROM inv.warehouses WHERE id = NEW.warehouse_id FOR SHARE;
        IF NOT w.is_active THEN
            RAISE EXCEPTION 'المخزن «% %» موقوف — اختار مخزن نشط.', w.code, w.name_ar;
        END IF;
        IF w.is_virtual THEN
            RAISE EXCEPTION 'المخزن «% %» وهمي — البيع منه في «الصرف الوهمي» (اختار مخزن حقيقي).', w.code, w.name_ar;
        END IF;
    END IF;

    IF TG_OP = 'INSERT' OR NEW.item_id <> OLD.item_id OR NEW.unit_id <> OLD.unit_id OR NEW.factor <> OLD.factor THEN
        IF NEW.unit_id = it.unit_id THEN
            f := 1;
        ELSE
            SELECT factor INTO f FROM inv.item_units WHERE item_id = NEW.item_id AND unit_id = NEW.unit_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'الوحدة دي مش من وحدات الصنف % «%».', it.code, it.name;
            END IF;
        END IF;
        IF NEW.factor <> f THEN
            RAISE EXCEPTION 'معامل الوحدة للصنف % «%» اتغيّر — افتح الفاتورة من جديد.', it.code, it.name;
        END IF;
    END IF;

    -- الضريبة: البيان من غير ض.ق.م · الكود «إضافة» للمبيعات ونشط · النسبة = نسبة الكود.
    IF h.stock_kind = 'statement' AND (NEW.tax_code_id IS NOT NULL OR NEW.vat_amount <> 0) THEN
        RAISE EXCEPTION 'الصنف % «%»: «البيان» من غير ضريبة قيمة مضافة.', it.code, it.name;
    END IF;
    IF NEW.tax_code_id IS NULL THEN
        IF NEW.vat_amount <> 0 THEN
            RAISE EXCEPTION 'الصنف % «%»: من غير كود ضريبة — الضريبة لازم صفر.', it.code, it.name;
        END IF;
    ELSIF TG_OP = 'INSERT' OR NEW.tax_code_id IS DISTINCT FROM OLD.tax_code_id OR NEW.tax_rate <> OLD.tax_rate THEN
        SELECT code, kind, rate, applies_to, is_active INTO tc FROM acc.tax_codes WHERE id = NEW.tax_code_id FOR SHARE;
        IF tc.kind <> 'add' OR tc.applies_to NOT IN ('both', 'sales') THEN
            RAISE EXCEPTION 'كود الضريبة % مش لضريبة إضافة على المبيعات.', tc.code;
        END IF;
        IF NOT tc.is_active AND (TG_OP = 'INSERT' OR NEW.tax_code_id IS DISTINCT FROM OLD.tax_code_id) THEN
            RAISE EXCEPTION 'كود الضريبة % موقوف.', tc.code;
        END IF;
        IF NEW.tax_rate <> tc.rate THEN
            RAISE EXCEPTION 'نسبة كود الضريبة % اتغيّرت — افتح الفاتورة من جديد.', tc.code;
        END IF;
    END IF;

    SELECT code INTO base FROM core.currencies WHERE is_base;
    IF h.currency_mode = 'single' AND (NEW.currency_code <> h.currency_code OR NEW.fx_rate <> h.fx_rate) THEN
        RAISE EXCEPTION 'الصنف % «%»: عملة السطر وسعرها لازم زي الفاتورة (% × %).', it.code, it.name,
            h.currency_code, h.fx_rate;
    END IF;
    IF NEW.currency_code = base AND NEW.fx_rate <> 1 THEN
        RAISE EXCEPTION 'العملة الأساسية (%) سعرها 1.', base;
    END IF;
    IF h.currency_mode = 'multi' AND NEW.header_discount <> 0 THEN
        RAISE EXCEPTION 'الفاتورة «متعددة العملات» مالهاش خصم فاتورة — الخصم على السطر بس.';
    END IF;
    RETURN NEW;
END $$;

-- المرحّلة: حركة مخزون لكل صنف مخزني + قيد مرحّل = الإجمالي + الخصم + تكلفة الحركات (+ قيد قبض لو نقدي) ·
-- المسودة مالهاش (آخر الـ transaction).
CREATE FUNCTION sales.sales_invoices_post_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    p      record;
    ns     integer;
    nm     integer;
    cogs   numeric;
    total  numeric;
    disc   numeric;
    hd     numeric;
    bad    integer;
BEGIN
    SELECT id, status, invoice_no, draft_no, pay_kind, total_base, discount_base, discount_amount, currency_mode,
           currency_code, fx_rate
      INTO v FROM sales.sales_invoices WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT count(*), coalesce(-sum(booked_value), 0) INTO nm, cogs
      FROM inv.stock_movements WHERE source_type = 'sales_invoice' AND source_id = v.id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'sales_invoice' AND source_id = v.id;
    SELECT status, total_debit INTO p FROM acc.journal_entries WHERE source_type = 'sales_receipt' AND source_id = v.id;
    IF v.status = 'posted' THEN
        SELECT count(*) FILTER (WHERE NOT i.is_service), coalesce(sum(l.total_base), 0),
               coalesce(sum(l.discount_base), 0), coalesce(sum(l.header_discount), 0),
               count(*) FILTER (WHERE v.currency_mode = 'single'
                                  AND (l.currency_code <> v.currency_code OR l.fx_rate <> v.fx_rate))
          INTO ns, total, disc, hd, bad
          FROM sales.sales_invoice_lines l JOIN inv.items i ON i.id = l.item_id WHERE l.invoice_id = v.id;
        IF bad > 0 THEN
            RAISE EXCEPTION 'فاتورة البيع %: فيه سطور عملتها أو سعرها مش زي الفاتورة.', v.invoice_no;
        END IF;
        IF total <> v.total_base OR disc <> v.discount_base
           OR (v.currency_mode = 'single' AND hd <> v.discount_amount) THEN
            RAISE EXCEPTION 'فاتورة البيع %: إجمالياتها مش زي مجموع سطورها — افتحها واحفظها تاني.', v.invoice_no;
        END IF;
        IF nm <> ns THEN
            RAISE EXCEPTION 'فاتورة البيع % مرحّلة من غير حركات المخزون بتاعتها — الترحيل لازم يعملها في نفس العملية.',
                v.invoice_no;
        END IF;
        IF v.total_base + v.discount_base + cogs <> 0
           AND (e.status IS DISTINCT FROM 'posted' OR e.total_debit <> v.total_base + v.discount_base + cogs) THEN
            RAISE EXCEPTION 'فاتورة البيع % مرحّلة من غير قيدها (الإجمالي % + الخصم % + التكلفة %) — الترحيل لازم يعمل القيد في نفس العملية.',
                v.invoice_no, v.total_base, v.discount_base, cogs;
        END IF;
        IF v.pay_kind = 'cash' AND v.total_base <> 0 THEN
            IF p.status IS DISTINCT FROM 'posted' OR p.total_debit <> v.total_base THEN
                RAISE EXCEPTION 'فاتورة البيع % نقدية ومرحّلة من غير قيد القبض بتاعها.', v.invoice_no;
            END IF;
        ELSIF p.status IS NOT NULL THEN
            RAISE EXCEPTION 'فاتورة البيع % آجلة وليها قيد قبض نقدي.', v.invoice_no;
        END IF;
    ELSIF nm > 0 OR e.status IS NOT NULL OR p.status IS NOT NULL THEN
        RAISE EXCEPTION 'فاتورة البيع % مسودة وليها حركات مخزون أو قيد — إلغاء الترحيل لازم يمسحهم في نفس العملية.',
            coalesce(v.invoice_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER sales_invoices_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON sales.sales_invoices
    FOR EACH ROW EXECUTE FUNCTION sales.sales_invoices_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON sales.sales_invoices
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER sales_invoices_post_trg AFTER INSERT OR UPDATE ON sales.sales_invoices
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION sales.sales_invoices_post_check();
CREATE TRIGGER sales_invoice_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON sales.sales_invoice_lines
    FOR EACH ROW EXECUTE FUNCTION sales.sales_invoice_lines_guard();

-- ---------------------------------------------------------------------------
-- 4) السنة المالية: نفس الحماية + فواتير البيع
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  text[];
    c  integer;
    n  integer := 0;
    docs constant text[][] := ARRAY[['cash.customer_receipts', 'voucher_date'], ['cash.supplier_payments', 'voucher_date'],
                                    ['cash.customer_payments', 'voucher_date'], ['cash.cash_transfers', 'transfer_date'],
                                    ['cash.cheques', 'cheque_date'], ['cash.custody_disbursements', 'disb_date'],
                                    ['cash.custody_settlements', 'settle_date'], ['cash.partner_transactions', 'txn_date'],
                                    ['acc.profit_distributions', 'dist_date'], ['inv.stock_transfers', 'transfer_date'],
                                    ['inv.stock_counts', 'count_date'], ['purch.purchase_invoices', 'invoice_date'],
                                    ['purch.purchase_returns', 'return_date'], ['sales.sales_invoices', 'invoice_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير ومرتجعات شراء وفواتير بيع) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
                n, OLD.year_code;
        END IF;
    END IF;
    IF OLD.status = 'open' AND NEW.status = 'closed' THEN
        n := 0;
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND status = ''draft''', t[1])
                INTO c USING OLD.id;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير ومرتجعات شراء وفواتير بيع) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
