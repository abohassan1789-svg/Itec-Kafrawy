-- 0048: «فاتورة الشراء» (المرحلة 7 — أول بند · النموذج 9 من mockups/phase7/02_purchase_invoice_ksa.html
--       «بيانات الفاتورة بتبويبات»، اختيار المستخدم 2026-11-27) + **العملة على سطر القيد**.
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (المستخدم اختار النموذج 9 + فكرة العملات، وباقي الأسئلة بالاقتراح):
--   * الفاتورة **بتدخّل البضاعة المخزن** (دفتر المخزون 0043 — تكلفة الوحدة = صافي السطر بالجنيه ÷ الكمية بالوحدة
--     الأساسية) **وبتعمل القيد لوحدها**: مدين المخزون (من نوع الصنف) · الصنف الخدمي مدين «تكلفة» نوع الصنف من غير
--     مخزون · مدين ض.ق.م مدخلات (من كود الضريبة) / دائن المورد (حساب رقابة مجموعته + بُعد المورد).
--   * «نقدي» = **قيد صرف لوحده** (نوعه «صرف فاتورة شراء نقدية») بيترحّل ويتلغي مع الفاتورة: مدين المورد / دائن
--     الخزنة أو البنك (بالعملة الأساسية زي السندات) — بيبان في كشف المورد والخزنة كحركتين.
--   * **فاتورة / بيان** = مخزون منفصل · البيان **من غير ض.ق.م** خالص · «شامل / غير شامل» على الفاتورة كلها ·
--     كود ضريبة لكل سطر (من الصنف، والمورد اللي ضريبته 0% سطوره بتبدأ بكوده) · خصم % على السطر + خصم على الفاتورة
--     **بيتوزّع على الأصناف بالقيمة ويقلّل تكلفتها** (مش 4302) · خصم المنبع مش على الفاتورة دلوقتي.
--   * **العملات (طلب المستخدم):**
--       (أ) «عملة واحدة» (الافتراضي): أي عملة للفاتورة كلها بسعر صرف بتاريخها (core.rate_on ويتعدّل) — كل السطور
--           بنفس العملة والسعر، والقيد بالجنيه = المبلغ × السعر.
--       (ب) «متعدد العملات»: كل سطر ليه عملته وسعره، والإجماليات بالجنيه (+ تفصيل لكل عملة) · خصم الفاتورة مش متاح
--           (خصم السطر % بس) عشان مايتوزّعش على عملات مختلفة.
--     **رصيد المورد**: سطر دائن على المورد **لكل (عملة + سعر)** بالجنيه ومعاه العملة والسعر والمبلغ بالعملة —
--     يعني دين المورد بيتسجل بعملاته الحقيقية (100 دولار + 50 يورو) والجنيه مجموعهم بسعر الفاتورة، وفروق العملة
--     بتتحسب وقت السداد (المرحلة 8). الضريبة والمخزون بالجنيه برضه ومعاهم العملة (للمراجعة).
--     ← عشان كده سطر القيد بقى فيه **العملة والسعر والمبلغ بالعملة** (قرار «تعدد العملات الكامل» — ملف 03 القسم 9.5).
--   * المخزن الافتراضي من الرأس وكل سطر يتغيّر · **المخزن الوهمي ممنوع** في الشراء · أي وحدة من وحدات الصنف بمعاملها.
--   * الرقم PUR-2026-00001 مع أول ترحيل (ويبدأ من 1 كل سنة) · «رقم فاتورة المورد» اختياري و**مايتكررش لنفس المورد** ·
--     «الرقم الإلكتروني» اختياري · أمر التوريد اختياري · الاستحقاق = التاريخ + مدة سداد المورد ويتعدّل.
--   * مسودة ← ترحيل ← «إلغاء الترحيل» بالصلاحية (ولو البضاعة اتصرفت محتاج «من غير رصيد») · المرحّلة ماتتعدّلش.
-- الحماية في القاعدة نفسها:
--   * السنة من التاريخ ولازم مفتوحة · المرحّل مايتعدّلش ولا يتمسح ولا سطوره · الرقم ثابت وفي نفس السنة.
--   * المورد نشط · الخزنة / البنك نشط وبالعملة الأساسية · المخزن نشط ومش وهمي · الصنف نشط · الوحدة بمعاملها ·
--     كود الضريبة «إضافة» للمشتريات بنسبته · عملة السطر = عملة الفاتورة في «عملة واحدة» · مبالغ السطر متسقة.
--   * **المرحّلة لازم ليها حركة مخزون لكل صنف مخزني + قيد مرحّل بالإجمالي (+ قيد الصرف لو نقدي) · والمسودة مالهاش
--     ولا ده ولا ده** (Constraint Trigger مؤجل).
--   * السنة ماتتقفلش وفيها فواتير مسودة، وتواريخها ماتسيبش فاتورة برّاها.

-- ---------------------------------------------------------------------------
-- 1) العملة على سطر القيد (قرار «تعدد العملات الكامل»)
-- ---------------------------------------------------------------------------
ALTER TABLE acc.journal_lines
    ADD COLUMN currency_code char(3)        REFERENCES core.currencies (code) ON DELETE RESTRICT,
    ADD COLUMN fx_rate       numeric(18,6),
    ADD COLUMN amount_fc     numeric(18,2),
    ADD CONSTRAINT journal_lines_fc_chk CHECK (
        (currency_code IS NULL) = (fx_rate IS NULL) AND (currency_code IS NULL) = (amount_fc IS NULL)
        AND (fx_rate IS NULL OR fx_rate > 0) AND (amount_fc IS NULL OR amount_fc > 0));
CREATE INDEX journal_lines_currency_idx ON acc.journal_lines (currency_code) WHERE currency_code IS NOT NULL;
COMMENT ON COLUMN acc.journal_lines.currency_code IS 'عملة السطر لو المستند بعملة غير الأساسية (المبلغ بالجنيه في مدين/دائن)';
COMMENT ON COLUMN acc.journal_lines.amount_fc IS 'المبلغ بالعملة (موجب — في نفس جنب السطر)';

-- ---------------------------------------------------------------------------
-- 2) قواعد الترحيل: الصنف الخدمي + قيد الصرف النقدي
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('purchase_invoice', 'service', 'تكلفة الخدمات المشتراة', 'dr', 'category', NULL, 'cogs', NULL, 6);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('purchase_payment', 'صرف فاتورة شراء نقدية', (SELECT max(sort_order) + 1 FROM acc.posting_doc_types));
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('purchase_payment', 'payable', 'الموردين', 'dr', 'party',    NULL, NULL, 'supplier', 1),
    ('purchase_payment', 'cash',    'الخزينة',  'cr', 'treasury', NULL, NULL, NULL,       2),
    ('purchase_payment', 'bank',    'البنك',    'cr', 'bank',     NULL, NULL, NULL,       3);

-- ---------------------------------------------------------------------------
-- 3) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE purch.purchase_invoices_draft_seq AS integer;
CREATE TABLE purch.purchase_invoices (
    id                   integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_no           varchar(30),                                  -- PUR-2026-00001 (من أول ترحيل)
    draft_no             integer        NOT NULL DEFAULT nextval('purch.purchase_invoices_draft_seq'),
    invoice_date         date           NOT NULL,
    fiscal_year_id       integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    supplier_id          integer        NOT NULL REFERENCES party.suppliers (id) ON DELETE RESTRICT,
    supplier_invoice_no  varchar(40),                                  -- رقمها عند المورد (اختياري — مايتكررش له)
    e_invoice_no         varchar(60),                                  -- الرقم الإلكتروني (اختياري)
    stock_kind           varchar(10)    NOT NULL DEFAULT 'invoice',     -- invoice فاتورة · statement بيان
    prices_include_vat   boolean        NOT NULL DEFAULT false,         -- الأسعار شاملة الضريبة
    pay_kind             varchar(10)    NOT NULL DEFAULT 'credit',      -- credit آجل · cash نقدي
    due_date             date,                                         -- الاستحقاق (الآجل بس)
    source_kind          varchar(10),                                  -- نقدي: treasury خزنة · bank بنك
    treasury_id          integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id      integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    warehouse_id         integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- المخزن الافتراضي للسطور
    project_id           integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,     -- أمر التوريد (اختياري)
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
    description          varchar(500),                                  -- ملاحظات (اختياري)
    status               varchar(10)    NOT NULL DEFAULT 'draft',
    created_by           integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at            timestamptz,
    posted_by            integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at           timestamptz    NOT NULL DEFAULT now(),
    updated_at           timestamptz    NOT NULL DEFAULT now(),
    row_version          integer        NOT NULL DEFAULT 1 CONSTRAINT purchase_invoices_row_version_chk CHECK (row_version > 0),
    CONSTRAINT purchase_invoices_no_unique UNIQUE (invoice_no),
    CONSTRAINT purchase_invoices_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT purchase_invoices_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT purchase_invoices_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT purchase_invoices_pay_chk CHECK (
        (pay_kind = 'credit' AND due_date IS NOT NULL AND due_date >= invoice_date AND source_kind IS NULL
         AND treasury_id IS NULL AND bank_account_id IS NULL)
        OR (pay_kind = 'cash' AND due_date IS NULL AND (
            (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
            OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)))),
    CONSTRAINT purchase_invoices_mode_chk CHECK (currency_mode IN ('single', 'multi')
        AND (currency_mode = 'single' OR (fx_rate = 1 AND discount_amount = 0))),
    CONSTRAINT purchase_invoices_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT purchase_invoices_amounts_chk CHECK (
        discount_amount >= 0 AND gross_amount >= 0 AND line_discount >= 0 AND net_amount >= 0 AND vat_amount >= 0
        AND total_amount = net_amount + vat_amount AND net_base >= 0 AND vat_base >= 0
        AND total_base = net_base + vat_base),
    CONSTRAINT purchase_invoices_sinv_chk CHECK (supplier_invoice_no IS NULL OR btrim(supplier_invoice_no) <> ''),
    CONSTRAINT purchase_invoices_einv_chk CHECK (e_invoice_no IS NULL OR btrim(e_invoice_no) <> ''),
    CONSTRAINT purchase_invoices_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT purchase_invoices_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR invoice_no IS NOT NULL))
);
ALTER SEQUENCE purch.purchase_invoices_draft_seq OWNED BY purch.purchase_invoices.draft_no;
COMMENT ON TABLE purch.purchase_invoices IS
    'فاتورة الشراء: مورد + أصناف بمخازنها · مسودة ← مرحّلة بحركات دفتر المخزون وقيد (وقيد صرف لو نقدي) · عملة واحدة أو متعدد';

CREATE TABLE purch.purchase_invoice_lines (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invoice_id       integer        NOT NULL REFERENCES purch.purchase_invoices (id) ON DELETE CASCADE,
    line_no          smallint       NOT NULL,
    item_id          integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    warehouse_id     integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- الخدمي مالوش مخزن
    unit_id          integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,
    factor           numeric(18,6)  NOT NULL DEFAULT 1,                 -- الوحدة = كام وحدة أساسية (وقت الكتابة)
    qty              numeric(18,6)  NOT NULL,                           -- بالوحدة المختارة
    base_qty         numeric(18,6)  GENERATED ALWAYS AS (round(qty * factor, 6)) STORED,
    unit_price       numeric(18,6)  NOT NULL,                           -- سعر الوحدة المختارة بعملة السطر
    discount_pct     numeric(7,4)   NOT NULL DEFAULT 0,
    tax_code_id      integer        REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,     -- الفاضي = من غير ضريبة
    tax_rate         numeric(7,4)   NOT NULL DEFAULT 0,                 -- نسبة الكود وقت الكتابة
    currency_code    char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    fx_rate          numeric(18,6)  NOT NULL DEFAULT 1,
    -- بيحسبها الـ Service (بعملة السطر، وبعدين بالجنيه):
    gross_amount     numeric(18,2)  NOT NULL,                           -- الكمية × السعر
    line_discount    numeric(18,2)  NOT NULL DEFAULT 0,
    header_discount  numeric(18,2)  NOT NULL DEFAULT 0,                 -- نصيبه من خصم الفاتورة
    net_amount       numeric(18,2)  NOT NULL,                           -- قبل الضريبة بعد الخصومات
    vat_amount       numeric(18,2)  NOT NULL DEFAULT 0,
    total_amount     numeric(18,2)  NOT NULL,
    net_base         numeric(18,2)  NOT NULL,
    vat_base         numeric(18,2)  NOT NULL DEFAULT 0,
    total_base       numeric(18,2)  NOT NULL,
    unit_cost        numeric(18,6)  NOT NULL DEFAULT 0,                 -- تكلفة الوحدة الأساسية بالجنيه
    CONSTRAINT purchase_invoice_lines_no_unique UNIQUE (invoice_id, line_no),
    CONSTRAINT purchase_invoice_lines_line_chk CHECK (line_no > 0),
    CONSTRAINT purchase_invoice_lines_factor_chk CHECK (factor > 0),
    CONSTRAINT purchase_invoice_lines_qty_chk CHECK (qty > 0 AND round(qty * factor, 6) > 0),
    CONSTRAINT purchase_invoice_lines_price_chk CHECK (unit_price >= 0 AND unit_cost >= 0),
    CONSTRAINT purchase_invoice_lines_disc_chk CHECK (discount_pct >= 0 AND discount_pct <= 100),
    CONSTRAINT purchase_invoice_lines_tax_chk CHECK (tax_rate >= 0 AND (tax_code_id IS NOT NULL OR tax_rate = 0)),
    CONSTRAINT purchase_invoice_lines_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT purchase_invoice_lines_amounts_chk CHECK (
        gross_amount >= 0 AND line_discount >= 0 AND header_discount >= 0 AND line_discount <= gross_amount
        AND net_amount >= 0 AND vat_amount >= 0 AND total_amount = net_amount + vat_amount
        AND net_base = round(net_amount * fx_rate, 2) AND vat_base = round(vat_amount * fx_rate, 2)
        AND total_base = net_base + vat_base)
);
COMMENT ON TABLE purch.purchase_invoice_lines IS
    'أصناف فاتورة الشراء: الكمية بالوحدة × المعامل · السعر بعملة السطر · المبالغ بالعملة وبالجنيه · تكلفة الوحدة الأساسية';

-- القائمة بالفترة (الأحدث فوق) + index على كل FK وعلى البحث.
CREATE INDEX purchase_invoices_date_idx ON purch.purchase_invoices (invoice_date DESC, id DESC);
CREATE INDEX purchase_invoices_year_idx ON purch.purchase_invoices (fiscal_year_id, status);
CREATE INDEX purchase_invoices_supplier_idx ON purch.purchase_invoices (supplier_id, invoice_date DESC);
CREATE UNIQUE INDEX purchase_invoices_supplier_inv_unique ON purch.purchase_invoices
    (supplier_id, lower(btrim(supplier_invoice_no))) WHERE supplier_invoice_no IS NOT NULL;
CREATE INDEX purchase_invoices_einv_idx ON purch.purchase_invoices (e_invoice_no) WHERE e_invoice_no IS NOT NULL;
CREATE INDEX purchase_invoices_treasury_idx ON purch.purchase_invoices (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX purchase_invoices_bank_idx ON purch.purchase_invoices (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX purchase_invoices_warehouse_idx ON purch.purchase_invoices (warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX purchase_invoices_project_idx ON purch.purchase_invoices (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX purchase_invoices_currency_idx ON purch.purchase_invoices (currency_code);
CREATE INDEX purchase_invoices_created_by_idx ON purch.purchase_invoices (created_by);
CREATE INDEX purchase_invoices_posted_by_idx ON purch.purchase_invoices (posted_by);
CREATE INDEX purchase_invoices_due_idx ON purch.purchase_invoices (due_date) WHERE pay_kind = 'credit';
CREATE INDEX purchase_invoices_desc_idx ON purch.purchase_invoices (lower(description) varchar_pattern_ops)
    WHERE description IS NOT NULL;
CREATE INDEX purchase_invoice_lines_item_idx ON purch.purchase_invoice_lines (item_id);
CREATE INDEX purchase_invoice_lines_warehouse_idx ON purch.purchase_invoice_lines (warehouse_id);
CREATE INDEX purchase_invoice_lines_unit_idx ON purch.purchase_invoice_lines (unit_id);
CREATE INDEX purchase_invoice_lines_tax_idx ON purch.purchase_invoice_lines (tax_code_id);
CREATE INDEX purchase_invoice_lines_currency_idx ON purch.purchase_invoice_lines (currency_code);

-- ---------------------------------------------------------------------------
-- 4) الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION purch.purchase_invoices_guard() RETURNS trigger
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
        lbl := 'فاتورة الشراء ' || coalesce(OLD.invoice_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
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

    -- السنة المالية من التاريخ (لازم مفتوحة).
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
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION '% مرحّلة — ماتتعدّلش (الغي ترحيلها الأول).', lbl;
                END IF;
                RETURN NEW;
            END IF;
            -- إلغاء الترحيل: الحالة بس (الفاتورة بترجع مسودة برقمها).
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
            IF NOT EXISTS (SELECT 1 FROM purch.purchase_invoice_lines WHERE invoice_id = NEW.id) THEN
                RAISE EXCEPTION '% مافيهاش أصناف — ضيف صنف واحد على الأقل قبل الترحيل.', lbl;
            END IF;
            IF NEW.invoice_no IS NULL THEN
                NEW.invoice_no := core.next_document_no('purchase_invoice', NEW.invoice_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- المورد: وقت الإضافة أو التغيير أو الترحيل.
    IF TG_OP = 'INSERT' OR posting OR NEW.supplier_id IS DISTINCT FROM OLD.supplier_id THEN
        SELECT code, name_ar, is_active INTO m FROM party.suppliers WHERE id = NEW.supplier_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'المورد مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
        END IF;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'المورد «% %» موقوف — اختار مورد نشط.', m.code, m.name_ar;
        END IF;
    END IF;

    -- العملة: «متعدد» = الفاتورة بالجنيه (كل سطر بعملته) · العملة الأساسية سعرها 1 · العملة نشطة.
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
            RAISE EXCEPTION '«% %» بعملة % — الدفع النقدي من خزنة / بنك بالعملة الأساسية (%) بس.',
                m.code, m.name_ar, m.currency_code, base;
        END IF;
    END IF;

    -- أمر التوريد (اختياري): جاري وقت الإضافة أو التغيير أو الترحيل.
    IF NEW.project_id IS NOT NULL AND (TG_OP = 'INSERT' OR posting OR NEW.project_id IS DISTINCT FROM OLD.project_id) THEN
        SELECT code, status INTO m FROM acc.projects WHERE id = NEW.project_id FOR SHARE;
        IF m.status IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'أمر التوريد % مش جاري — اختار أمر جاري أو سيبه فاضي.', m.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- السطور: الفاتورة لازم مسودة · الصنف نشط · المخزن (للمخزني) نشط ومش وهمي · الوحدة بمعاملها · الضريبة · العملة.
CREATE FUNCTION purch.purchase_invoice_lines_guard() RETURNS trigger
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
      FROM purch.purchase_invoices
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.invoice_id ELSE NEW.invoice_id END;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- الفاتورة نفسها اتمسحت (CASCADE)
    END IF;
    IF h.status <> 'draft' THEN
        RAISE EXCEPTION 'فاتورة الشراء % مرحّلة — أصنافها ماتتعدّلش (الغي ترحيلها الأول).',
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
        RAISE EXCEPTION 'الصنف % «%» موقوف — مايتشتراش.', it.code, it.name;
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
            RAISE EXCEPTION 'المخزن «% %» وهمي — مالوش مشتريات (اشتري على مخزن حقيقي وحوّل بعدين).', w.code, w.name_ar;
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

    -- الضريبة: البيان من غير ض.ق.م · الكود «إضافة» للمشتريات ونشط · النسبة = نسبة الكود.
    IF h.stock_kind = 'statement' AND (NEW.tax_code_id IS NOT NULL OR NEW.vat_amount <> 0) THEN
        RAISE EXCEPTION 'الصنف % «%»: «البيان» من غير ضريبة قيمة مضافة.', it.code, it.name;
    END IF;
    IF NEW.tax_code_id IS NULL THEN
        IF NEW.vat_amount <> 0 THEN
            RAISE EXCEPTION 'الصنف % «%»: من غير كود ضريبة — الضريبة لازم صفر.', it.code, it.name;
        END IF;
    ELSIF TG_OP = 'INSERT' OR NEW.tax_code_id IS DISTINCT FROM OLD.tax_code_id OR NEW.tax_rate <> OLD.tax_rate THEN
        SELECT code, kind, rate, applies_to, is_active INTO tc FROM acc.tax_codes WHERE id = NEW.tax_code_id FOR SHARE;
        IF tc.kind <> 'add' OR tc.applies_to NOT IN ('both', 'purch') THEN
            RAISE EXCEPTION 'كود الضريبة % مش لضريبة إضافة على المشتريات.', tc.code;
        END IF;
        IF NOT tc.is_active AND (TG_OP = 'INSERT' OR NEW.tax_code_id IS DISTINCT FROM OLD.tax_code_id) THEN
            RAISE EXCEPTION 'كود الضريبة % موقوف.', tc.code;
        END IF;
        IF NEW.tax_rate <> tc.rate THEN
            RAISE EXCEPTION 'نسبة كود الضريبة % اتغيّرت — افتح الفاتورة من جديد.', tc.code;
        END IF;
    END IF;

    -- العملة: «عملة واحدة» = عملة الفاتورة وسعرها · الأساسية سعرها 1.
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

-- المرحّلة: حركة مخزون لكل صنف مخزني + قيد مرحّل بالإجمالي (+ قيد صرف لو نقدي) · المسودة مالهاش (آخر الـ transaction).
CREATE FUNCTION purch.purchase_invoices_post_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    p      record;
    ns     integer;
    nm     integer;
    total  numeric;
    hd     numeric;
    bad    integer;
BEGIN
    SELECT id, status, invoice_no, draft_no, pay_kind, total_base, discount_amount, currency_mode, currency_code, fx_rate
      INTO v FROM purch.purchase_invoices WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;                                     -- اتمسحت في نفس الـ transaction
    END IF;
    SELECT count(*) INTO nm FROM inv.stock_movements WHERE source_type = 'purchase_invoice' AND source_id = v.id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'purchase_invoice' AND source_id = v.id;
    SELECT status, total_debit INTO p FROM acc.journal_entries WHERE source_type = 'purchase_payment' AND source_id = v.id;
    IF v.status = 'posted' THEN
        SELECT count(*) FILTER (WHERE NOT i.is_service), coalesce(sum(l.total_base), 0), coalesce(sum(l.header_discount), 0),
               count(*) FILTER (WHERE v.currency_mode = 'single'
                                  AND (l.currency_code <> v.currency_code OR l.fx_rate <> v.fx_rate))
          INTO ns, total, hd, bad
          FROM purch.purchase_invoice_lines l JOIN inv.items i ON i.id = l.item_id WHERE l.invoice_id = v.id;
        IF bad > 0 THEN
            RAISE EXCEPTION 'فاتورة الشراء %: فيه سطور عملتها أو سعرها مش زي الفاتورة.', v.invoice_no;
        END IF;
        IF total <> v.total_base OR (v.currency_mode = 'single' AND hd <> v.discount_amount) THEN
            RAISE EXCEPTION 'فاتورة الشراء %: إجمالياتها مش زي مجموع سطورها — افتحها واحفظها تاني.', v.invoice_no;
        END IF;
        IF nm <> ns THEN
            RAISE EXCEPTION 'فاتورة الشراء % مرحّلة من غير حركات المخزون بتاعتها — الترحيل لازم يعملها في نفس العملية.',
                v.invoice_no;
        END IF;
        IF v.total_base <> 0 AND (e.status IS DISTINCT FROM 'posted' OR e.total_debit <> v.total_base) THEN
            RAISE EXCEPTION 'فاتورة الشراء % مرحّلة من غير قيدها بالإجمالي % — الترحيل لازم يعمل القيد في نفس العملية.',
                v.invoice_no, v.total_base;
        END IF;
        IF v.pay_kind = 'cash' AND v.total_base <> 0 THEN
            IF p.status IS DISTINCT FROM 'posted' OR p.total_debit <> v.total_base THEN
                RAISE EXCEPTION 'فاتورة الشراء % نقدية ومرحّلة من غير قيد الصرف بتاعها.', v.invoice_no;
            END IF;
        ELSIF p.status IS NOT NULL THEN
            RAISE EXCEPTION 'فاتورة الشراء % آجلة وليها قيد صرف نقدي.', v.invoice_no;
        END IF;
    ELSIF nm > 0 OR e.status IS NOT NULL OR p.status IS NOT NULL THEN
        RAISE EXCEPTION 'فاتورة الشراء % مسودة وليها حركات مخزون أو قيد — إلغاء الترحيل لازم يمسحهم في نفس العملية.',
            coalesce(v.invoice_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER purchase_invoices_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON purch.purchase_invoices
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_invoices_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON purch.purchase_invoices
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER purchase_invoices_post_trg AFTER INSERT OR UPDATE ON purch.purchase_invoices
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION purch.purchase_invoices_post_check();
CREATE TRIGGER purchase_invoice_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON purch.purchase_invoice_lines
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_invoice_lines_guard();

-- ---------------------------------------------------------------------------
-- 5) السنة المالية: نفس حماية السندات + فواتير الشراء
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
                                    ['inv.stock_counts', 'count_date'], ['purch.purchase_invoices', 'invoice_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير شراء) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير شراء) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
