-- 0049: «مرتجع المشتريات» (المرحلة 7 — النموذج 1 من mockups/phase7/03_purchase_return.html «زي فاتورة الشراء
--       بالظبط»، اختيار المستخدم 2026-11-28) + **خارج بتكلفة من المستند** في دفتر المخزون.
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- ردود المستخدم (2026-11-28):
--   * **الربط بفاتورة الشراء الأصلية اختياري** — كل سطر ممكن يتربط بسطر من فاتورة مرحّلة لنفس المورد، أو من غير فاتورة.
--   * **أكتر من فاتورة في المرتجع الواحد عادي** (الربط على مستوى السطر).
--   * **السعر = سعر الفاتورة الأصلية ويتغيّر عادي** (الـ Service بيملّيه بصافي سعر الوحدة بعد خصم السطر ونصيبه من خصم
--     الفاتورة — قبل الضريبة) · مفيش خصم على المرتجع.
--   * **البضاعة تطلع بنفس سعر الوحدة اللي اتشرت بيه (مش بالمتوسط)** ← تكلفة الخروج = صافي السطر بالجنيه ÷ الكمية
--     بالوحدة الأساسية (cost_mode = given_out) والمتوسط بيتحسب من الباقي · لو السعر اتغيّر التكلفة بتمشي معاه ·
--     الفرق اللي ممكن يطلع (المرتجع بيصفّر الرصيد / الباقي قيمته أقل) بيتقيّد على «فروق تكلفة المخزون».
--   * الكمية **مايتعدّاش اللي اتشرى ناقص اللي رجع قبل كده** لو السطر مربوط بفاتورة.
--   * الافتراضي «آجل» (رصيد المورد بيقل) · «نقدي» = المورد رجّع الفلوس ← **قيد قبض لوحده** (مدين الخزنة / البنك —
--     دائن المورد) بيترحّل ويتلغي مع المرتجع (بالاقتراح — زي النقدي في فاتورة الشراء).
--   * **سبب المرتجع اختياري من قايمة المستخدم يقدر يزوّد عليها** (purch.return_reasons).
--   * PRT-2026-0001 كل سنة · «رقم إشعار المورد» اختياري · مسودة + ترحيل · المخزن الافتراضي = مخزن سطر الفاتورة ·
--     مفيش نقل من الأكسس · المكان «المشتريات».
--   * بالاقتراح: البضاعة اللي اتصرفت ← صلاحية «من غير رصيد» · **فاتورة الشراء مايتلغيش ترحيلها وعليها مرتجع**.
-- الحماية في القاعدة نفسها: زي فاتورة الشراء (السنة · المرحّل · الرقم · المورد · الخزنة · المخزن · الصنف · الوحدة ·
-- الضريبة · العملة) + السطر المربوط من فاتورة مرحّلة لنفس المورد والنوع والصنف والعملة وبسعر صرفها + الكمية المرتجعة
-- لكل سطر فاتورة ≤ اللي اتشرى (وقت الترحيل وبقفل الفاتورة) + المرحّل ليه حركات المخزون والقيد (+ القبض).

-- ---------------------------------------------------------------------------
-- 1) دفتر المخزون: خارج بتكلفة من المستند (given_out)
-- ---------------------------------------------------------------------------
ALTER TABLE inv.stock_movements DROP CONSTRAINT stock_movements_mode_chk;
ALTER TABLE inv.stock_movements DROP CONSTRAINT stock_movements_mode_rules;
ALTER TABLE inv.stock_movements
    ADD CONSTRAINT stock_movements_mode_chk CHECK (
        cost_mode IN ('given', 'given_out', 'average', 'transfer', 'current', 'none')),
    ADD CONSTRAINT stock_movements_mode_rules CHECK (
        (cost_mode = 'none') = is_virtual
        AND (cost_mode IN ('given', 'given_out')) = (given_cost IS NOT NULL)
        AND (given_cost IS NULL OR given_cost >= 0)
        AND (cost_mode NOT IN ('given', 'transfer', 'current') OR qty > 0)
        AND (cost_mode NOT IN ('average', 'given_out') OR qty < 0)
        AND (cost_mode NOT IN ('transfer', 'current') OR pair_id IS NOT NULL)
        AND (pair_id IS NULL OR qty > 0));
COMMENT ON COLUMN inv.stock_movements.cost_mode IS
    'given = داخل بتكلفة من المستند · given_out = خارج بتكلفة من المستند (مرتجع المشتريات بسعر الشراء) · '
    'average = خارج بالمتوسط · transfer = داخل بقيمة الخارج اللي قصاده · current = داخل بآخر متوسط (من مخزن وهمي) · '
    'none = مخزن وهمي (كمية بس)';

-- ---------------------------------------------------------------------------
-- 2) قواعد الترحيل: الصنف الخدمي + فروق التكلفة + قيد القبض النقدي
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('purchase_return', 'service',  'تكلفة الخدمات المشتراة', 'cr', 'category', NULL,       'cogs', NULL, 6),
    -- موجب = دائن (قيمة الخروج أقل من سعر المرتجع) · سالب = مدين.
    ('purchase_return', 'cost_var', 'فروق تكلفة المخزون',     'cr', 'default',  'cost_var', NULL,   NULL, 7);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('purchase_refund', 'قبض مرتجع مشتريات نقدي', (SELECT max(sort_order) + 1 FROM acc.posting_doc_types));
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('purchase_refund', 'cash',    'الخزينة',  'dr', 'treasury', NULL, NULL, NULL,       1),
    ('purchase_refund', 'bank',    'البنك',    'dr', 'bank',     NULL, NULL, NULL,       2),
    ('purchase_refund', 'payable', 'الموردين', 'cr', 'party',    NULL, NULL, 'supplier', 3);

-- ---------------------------------------------------------------------------
-- 3) أسباب المرتجع (قايمة المستخدم بيزوّد عليها)
-- ---------------------------------------------------------------------------
CREATE TABLE purch.return_reasons (
    id          integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name_ar     varchar(80)   NOT NULL,
    sort_order  integer       NOT NULL DEFAULT 0,
    is_active   boolean       NOT NULL DEFAULT true,
    created_at  timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT return_reasons_name_chk CHECK (btrim(name_ar) <> '' AND name_ar = btrim(name_ar))
);
CREATE UNIQUE INDEX return_reasons_name_unique ON purch.return_reasons (lower(name_ar));
COMMENT ON TABLE purch.return_reasons IS 'أسباب مرتجع المشتريات (اختياري على المرتجع — المستخدم بيزوّد ويعدّل)';
INSERT INTO purch.return_reasons (name_ar, sort_order) VALUES
    ('تالف / مكسور', 1), ('مش مطابق للمواصفات', 2), ('زيادة عن المطلوب', 3), ('غلط في الطلب', 4), ('تاني', 5);

-- ---------------------------------------------------------------------------
-- 4) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE purch.purchase_returns_draft_seq AS integer;
CREATE TABLE purch.purchase_returns (
    id                 integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_no          varchar(30),                                  -- PRT-2026-0001 (من أول ترحيل)
    draft_no           integer        NOT NULL DEFAULT nextval('purch.purchase_returns_draft_seq'),
    return_date        date           NOT NULL,
    fiscal_year_id     integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    supplier_id        integer        NOT NULL REFERENCES party.suppliers (id) ON DELETE RESTRICT,
    supplier_note_no   varchar(40),                                  -- رقم إشعار المورد (اختياري)
    reason_id          integer        REFERENCES purch.return_reasons (id) ON DELETE RESTRICT,   -- اختياري
    stock_kind         varchar(10)    NOT NULL DEFAULT 'invoice',     -- invoice فاتورة · statement بيان
    pay_kind           varchar(10)    NOT NULL DEFAULT 'credit',      -- credit آجل · cash نقدي (المورد رجّع فلوس)
    source_kind        varchar(10),                                  -- نقدي: treasury خزنة · bank بنك
    treasury_id        integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id    integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    warehouse_id       integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- الافتراضي للسطور الحرة
    project_id         integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,     -- أمر التوريد (اختياري)
    currency_code      char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    fx_rate            numeric(18,6)  NOT NULL DEFAULT 1,             -- للسطور الحرة (المربوط بسعر فاتورته)
    -- الإجماليات (بيحسبها الـ Service مع كل حفظ — بعملة المرتجع وبالجنيه):
    net_amount         numeric(18,2)  NOT NULL DEFAULT 0,
    vat_amount         numeric(18,2)  NOT NULL DEFAULT 0,
    total_amount       numeric(18,2)  NOT NULL DEFAULT 0,
    net_base           numeric(18,2)  NOT NULL DEFAULT 0,
    vat_base           numeric(18,2)  NOT NULL DEFAULT 0,
    total_base         numeric(18,2)  NOT NULL DEFAULT 0,
    description        varchar(500),
    status             varchar(10)    NOT NULL DEFAULT 'draft',
    created_by         integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at          timestamptz,
    posted_by          integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at         timestamptz    NOT NULL DEFAULT now(),
    updated_at         timestamptz    NOT NULL DEFAULT now(),
    row_version        integer        NOT NULL DEFAULT 1 CONSTRAINT purchase_returns_row_version_chk CHECK (row_version > 0),
    CONSTRAINT purchase_returns_no_unique UNIQUE (return_no),
    CONSTRAINT purchase_returns_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT purchase_returns_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT purchase_returns_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT purchase_returns_pay_chk CHECK (
        (pay_kind = 'credit' AND source_kind IS NULL AND treasury_id IS NULL AND bank_account_id IS NULL)
        OR (pay_kind = 'cash' AND (
            (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
            OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)))),
    CONSTRAINT purchase_returns_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT purchase_returns_amounts_chk CHECK (
        net_amount >= 0 AND vat_amount >= 0 AND total_amount = net_amount + vat_amount
        AND net_base >= 0 AND vat_base >= 0 AND total_base = net_base + vat_base),
    CONSTRAINT purchase_returns_note_chk CHECK (supplier_note_no IS NULL OR btrim(supplier_note_no) <> ''),
    CONSTRAINT purchase_returns_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT purchase_returns_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR return_no IS NOT NULL))
);
ALTER SEQUENCE purch.purchase_returns_draft_seq OWNED BY purch.purchase_returns.draft_no;
COMMENT ON TABLE purch.purchase_returns IS
    'مرتجع المشتريات: مورد + أصناف راجعة (مربوطة بفواتيرها أو حرة) · مسودة ← مرحّل بحركات خروج بسعر الشراء وقيد (+ قبض لو نقدي)';

CREATE TABLE purch.purchase_return_lines (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id        integer        NOT NULL REFERENCES purch.purchase_returns (id) ON DELETE CASCADE,
    line_no          smallint       NOT NULL,
    source_line_id   integer        REFERENCES purch.purchase_invoice_lines (id) ON DELETE RESTRICT,   -- اختياري
    item_id          integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    warehouse_id     integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,   -- الخدمي مالوش مخزن
    unit_id          integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,
    factor           numeric(18,6)  NOT NULL DEFAULT 1,
    qty              numeric(18,6)  NOT NULL,
    base_qty         numeric(18,6)  GENERATED ALWAYS AS (round(qty * factor, 6)) STORED,
    unit_price       numeric(18,6)  NOT NULL,                           -- سعر الوحدة المختارة قبل الضريبة
    tax_code_id      integer        REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,
    tax_rate         numeric(7,4)   NOT NULL DEFAULT 0,
    currency_code    char(3)        NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    fx_rate          numeric(18,6)  NOT NULL DEFAULT 1,
    net_amount       numeric(18,2)  NOT NULL,
    vat_amount       numeric(18,2)  NOT NULL DEFAULT 0,
    total_amount     numeric(18,2)  NOT NULL,
    net_base         numeric(18,2)  NOT NULL,
    vat_base         numeric(18,2)  NOT NULL DEFAULT 0,
    total_base       numeric(18,2)  NOT NULL,
    unit_cost        numeric(18,6)  NOT NULL DEFAULT 0,                 -- تكلفة خروج الوحدة الأساسية بالجنيه
    CONSTRAINT purchase_return_lines_no_unique UNIQUE (return_id, line_no),
    CONSTRAINT purchase_return_lines_line_chk CHECK (line_no > 0),
    CONSTRAINT purchase_return_lines_factor_chk CHECK (factor > 0),
    CONSTRAINT purchase_return_lines_qty_chk CHECK (qty > 0 AND round(qty * factor, 6) > 0),
    CONSTRAINT purchase_return_lines_price_chk CHECK (unit_price >= 0 AND unit_cost >= 0),
    CONSTRAINT purchase_return_lines_tax_chk CHECK (tax_rate >= 0 AND (tax_code_id IS NOT NULL OR tax_rate = 0)),
    CONSTRAINT purchase_return_lines_rate_chk CHECK (fx_rate > 0),
    CONSTRAINT purchase_return_lines_amounts_chk CHECK (
        net_amount >= 0 AND vat_amount >= 0 AND total_amount = net_amount + vat_amount
        AND net_base = round(net_amount * fx_rate, 2) AND vat_base = round(vat_amount * fx_rate, 2)
        AND total_base = net_base + vat_base)
);
COMMENT ON TABLE purch.purchase_return_lines IS
    'أصناف مرتجع المشتريات: مربوطة بسطر فاتورة الشراء (اختياري) · الكمية بالوحدة × المعامل · تكلفة الخروج = سعر الشراء';

CREATE INDEX purchase_returns_date_idx ON purch.purchase_returns (return_date DESC, id DESC);
CREATE INDEX purchase_returns_year_idx ON purch.purchase_returns (fiscal_year_id, status);
CREATE INDEX purchase_returns_supplier_idx ON purch.purchase_returns (supplier_id, return_date DESC);
CREATE INDEX purchase_returns_reason_idx ON purch.purchase_returns (reason_id) WHERE reason_id IS NOT NULL;
CREATE INDEX purchase_returns_note_idx ON purch.purchase_returns (supplier_note_no) WHERE supplier_note_no IS NOT NULL;
CREATE INDEX purchase_returns_treasury_idx ON purch.purchase_returns (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX purchase_returns_bank_idx ON purch.purchase_returns (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX purchase_returns_warehouse_idx ON purch.purchase_returns (warehouse_id) WHERE warehouse_id IS NOT NULL;
CREATE INDEX purchase_returns_project_idx ON purch.purchase_returns (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX purchase_returns_currency_idx ON purch.purchase_returns (currency_code);
CREATE INDEX purchase_returns_created_by_idx ON purch.purchase_returns (created_by);
CREATE INDEX purchase_returns_posted_by_idx ON purch.purchase_returns (posted_by);
CREATE INDEX purchase_returns_desc_idx ON purch.purchase_returns (lower(description) varchar_pattern_ops)
    WHERE description IS NOT NULL;
CREATE INDEX purchase_return_lines_source_idx ON purch.purchase_return_lines (source_line_id)
    WHERE source_line_id IS NOT NULL;
CREATE INDEX purchase_return_lines_item_idx ON purch.purchase_return_lines (item_id);
CREATE INDEX purchase_return_lines_warehouse_idx ON purch.purchase_return_lines (warehouse_id);
CREATE INDEX purchase_return_lines_unit_idx ON purch.purchase_return_lines (unit_id);
CREATE INDEX purchase_return_lines_tax_idx ON purch.purchase_return_lines (tax_code_id);
CREATE INDEX purchase_return_lines_currency_idx ON purch.purchase_return_lines (currency_code);

-- ---------------------------------------------------------------------------
-- 5) الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION purch.purchase_returns_guard() RETURNS trigger
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
        lbl := 'المرتجع ' || coalesce(OLD.return_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — مرتجعاتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.return_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.return_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.return_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها مرتجع.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.return_no IS NOT NULL THEN
            RAISE EXCEPTION 'المرتجع الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.return_no IS NOT NULL AND NEW.return_no IS DISTINCT FROM OLD.return_no) THEN
            RAISE EXCEPTION 'رقم المرتجع ثابت.';
        END IF;
        IF OLD.return_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
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
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع المرتجع مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NOT EXISTS (SELECT 1 FROM purch.purchase_return_lines WHERE return_id = NEW.id) THEN
                RAISE EXCEPTION '% مافيهوش أصناف — ضيف صنف واحد على الأقل قبل الترحيل.', lbl;
            END IF;
            IF NEW.return_no IS NULL THEN
                NEW.return_no := core.next_document_no('purchase_return', NEW.return_date);
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    IF TG_OP = 'INSERT' OR posting OR NEW.supplier_id IS DISTINCT FROM OLD.supplier_id THEN
        SELECT code, name_ar, is_active INTO m FROM party.suppliers WHERE id = NEW.supplier_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'المورد مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
        END IF;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'المورد «% %» موقوف — اختار مورد نشط.', m.code, m.name_ar;
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.supplier_id <> OLD.supplier_id
       AND EXISTS (SELECT 1 FROM purch.purchase_return_lines WHERE return_id = NEW.id AND source_line_id IS NOT NULL) THEN
        RAISE EXCEPTION '% فيه أصناف مربوطة بفواتير المورد — شيلها الأول قبل ما تغيّر المورد.', lbl;
    END IF;

    SELECT code INTO base FROM core.currencies WHERE is_base;
    IF NEW.currency_code = base AND NEW.fx_rate <> 1 THEN
        RAISE EXCEPTION 'العملة الأساسية (%) سعرها 1.', base;
    END IF;
    IF TG_OP = 'INSERT' OR posting OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
        IF NOT (SELECT is_active FROM core.currencies WHERE code = NEW.currency_code) THEN
            RAISE EXCEPTION 'العملة % موقوفة — اختار عملة نشطة.', NEW.currency_code;
        END IF;
    END IF;

    IF NEW.reason_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.reason_id IS DISTINCT FROM OLD.reason_id) THEN
        IF NOT (SELECT is_active FROM purch.return_reasons WHERE id = NEW.reason_id) THEN
            RAISE EXCEPTION 'سبب المرتجع ده موقوف — اختار سبب تاني.';
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

    IF NEW.project_id IS NOT NULL AND (TG_OP = 'INSERT' OR posting OR NEW.project_id IS DISTINCT FROM OLD.project_id) THEN
        SELECT code, status INTO m FROM acc.projects WHERE id = NEW.project_id FOR SHARE;
        IF m.status IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'أمر التوريد % مش جاري — اختار أمر جاري أو سيبه فاضي.', m.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- السطور: المرتجع مسودة · الصنف · المخزن (مش وهمي) · الوحدة · الضريبة · العملة · والسطر المربوط من فاتورة مرحّلة
-- لنفس المورد والنوع والصنف والعملة وبسعر صرفها.
CREATE FUNCTION purch.purchase_return_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h    record;
    it   record;
    w    record;
    tc   record;
    s    record;
    f    numeric;
    base text;
BEGIN
    SELECT id, status, return_no, draft_no, supplier_id, stock_kind, currency_code, fx_rate INTO h
      FROM purch.purchase_returns
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.return_id ELSE NEW.return_id END;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;     -- المرتجع نفسه اتمسح (CASCADE)
    END IF;
    IF h.status <> 'draft' THEN
        RAISE EXCEPTION 'المرتجع % مرحّل — أصنافه ماتتعدّلش (الغي ترحيله الأول).',
            coalesce(h.return_no, 'مسودة ' || h.draft_no);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.return_id <> OLD.return_id THEN
        RAISE EXCEPTION 'السطر مايتنقلش لمرتجع تاني.';
    END IF;

    SELECT code, name, is_service, is_active, unit_id INTO it FROM inv.items WHERE id = NEW.item_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الصنف مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT it.is_active AND NEW.source_line_id IS NULL AND (TG_OP = 'INSERT' OR NEW.item_id <> OLD.item_id) THEN
        RAISE EXCEPTION 'الصنف % «%» موقوف.', it.code, it.name;
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
            RAISE EXCEPTION 'المخزن «% %» وهمي — مالوش مرتجع مشتريات.', w.code, w.name_ar;
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
            RAISE EXCEPTION 'معامل الوحدة للصنف % «%» اتغيّر — افتح المرتجع من جديد.', it.code, it.name;
        END IF;
    END IF;

    IF h.stock_kind = 'statement' AND (NEW.tax_code_id IS NOT NULL OR NEW.vat_amount <> 0) THEN
        RAISE EXCEPTION 'الصنف % «%»: «البيان» من غير ضريبة قيمة مضافة.', it.code, it.name;
    END IF;
    IF NEW.tax_code_id IS NULL THEN
        IF NEW.vat_amount <> 0 THEN
            RAISE EXCEPTION 'الصنف % «%»: من غير كود ضريبة — الضريبة لازم صفر.', it.code, it.name;
        END IF;
    ELSIF TG_OP = 'INSERT' OR NEW.tax_code_id IS DISTINCT FROM OLD.tax_code_id OR NEW.tax_rate <> OLD.tax_rate THEN
        SELECT code, kind, rate, applies_to INTO tc FROM acc.tax_codes WHERE id = NEW.tax_code_id FOR SHARE;
        IF tc.kind <> 'add' OR tc.applies_to NOT IN ('both', 'purch') THEN
            RAISE EXCEPTION 'كود الضريبة % مش لضريبة إضافة على المشتريات.', tc.code;
        END IF;
        IF NEW.tax_rate <> tc.rate THEN
            RAISE EXCEPTION 'نسبة كود الضريبة % اتغيّرت — افتح المرتجع من جديد.', tc.code;
        END IF;
    END IF;

    SELECT code INTO base FROM core.currencies WHERE is_base;
    IF NEW.currency_code <> h.currency_code THEN
        RAISE EXCEPTION 'الصنف % «%»: عملة السطر لازم زي المرتجع (%).', it.code, it.name, h.currency_code;
    END IF;
    IF NEW.currency_code = base AND NEW.fx_rate <> 1 THEN
        RAISE EXCEPTION 'العملة الأساسية (%) سعرها 1.', base;
    END IF;

    IF NEW.source_line_id IS NULL THEN
        IF NEW.fx_rate <> h.fx_rate THEN
            RAISE EXCEPTION 'الصنف % «%»: سعر صرف السطر الحر لازم زي المرتجع (%).', it.code, it.name, h.fx_rate;
        END IF;
    ELSE
        SELECT l.item_id, l.currency_code, l.fx_rate, v.status, v.supplier_id, v.stock_kind,
               coalesce(v.invoice_no, 'مسودة ' || v.draft_no) AS label INTO s
          FROM purch.purchase_invoice_lines l JOIN purch.purchase_invoices v ON v.id = l.invoice_id
         WHERE l.id = NEW.source_line_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'سطر الفاتورة الأصلية مش موجود (ممكن تكون الفاتورة اتعدّلت) — دوس «تحديث».';
        END IF;
        IF s.status <> 'posted' THEN
            RAISE EXCEPTION 'فاتورة الشراء % مش مرحّلة — المرتجع بيتربط بفاتورة مرحّلة بس.', s.label;
        END IF;
        IF s.supplier_id <> h.supplier_id THEN
            RAISE EXCEPTION 'فاتورة الشراء % لمورد تاني.', s.label;
        END IF;
        IF s.stock_kind <> h.stock_kind THEN
            RAISE EXCEPTION 'فاتورة الشراء % «%» والمرتجع «%» — لازم نفس النوع.', s.label,
                CASE s.stock_kind WHEN 'invoice' THEN 'فاتورة' ELSE 'بيان' END,
                CASE h.stock_kind WHEN 'invoice' THEN 'فاتورة' ELSE 'بيان' END;
        END IF;
        IF s.item_id <> NEW.item_id THEN
            RAISE EXCEPTION 'الصنف لازم زي سطر فاتورة الشراء %.', s.label;
        END IF;
        IF s.currency_code <> NEW.currency_code OR s.fx_rate <> NEW.fx_rate THEN
            RAISE EXCEPTION 'الصنف % «%»: العملة وسعرها لازم زي فاتورة الشراء % (% × %).', it.code, it.name,
                s.label, s.currency_code, s.fx_rate;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- المرحّل: حركة مخزون لكل صنف مخزني + قيد مرحّل (+ قيد القبض لو نقدي) + الكمية المرتجعة ≤ اللي اتشرى ·
-- المسودة مالهاش ولا ده ولا ده (آخر الـ transaction).
CREATE FUNCTION purch.purchase_returns_post_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    p      record;
    o      record;
    ns     integer;
    nm     integer;
    total  numeric;
BEGIN
    SELECT id, status, return_no, draft_no, pay_kind, total_base INTO v FROM purch.purchase_returns WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT count(*) INTO nm FROM inv.stock_movements WHERE source_type = 'purchase_return' AND source_id = v.id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'purchase_return' AND source_id = v.id;
    SELECT status, total_debit INTO p FROM acc.journal_entries WHERE source_type = 'purchase_refund' AND source_id = v.id;
    IF v.status = 'posted' THEN
        SELECT count(*) FILTER (WHERE NOT i.is_service), coalesce(sum(l.total_base), 0) INTO ns, total
          FROM purch.purchase_return_lines l JOIN inv.items i ON i.id = l.item_id WHERE l.return_id = v.id;
        IF total <> v.total_base THEN
            RAISE EXCEPTION 'المرتجع %: إجمالياته مش زي مجموع سطوره — افتحه واحفظه تاني.', v.return_no;
        END IF;
        IF nm <> ns THEN
            RAISE EXCEPTION 'المرتجع % مرحّل من غير حركات المخزون بتاعته — الترحيل لازم يعملها في نفس العملية.',
                v.return_no;
        END IF;
        IF v.total_base <> 0 AND (e.status IS DISTINCT FROM 'posted' OR e.total_debit < v.total_base) THEN
            RAISE EXCEPTION 'المرتجع % مرحّل من غير قيده بالإجمالي % — الترحيل لازم يعمل القيد في نفس العملية.',
                v.return_no, v.total_base;
        END IF;
        IF v.pay_kind = 'cash' AND v.total_base <> 0 THEN
            IF p.status IS DISTINCT FROM 'posted' OR p.total_debit <> v.total_base THEN
                RAISE EXCEPTION 'المرتجع % نقدي ومرحّل من غير قيد القبض بتاعه.', v.return_no;
            END IF;
        ELSIF p.status IS NOT NULL THEN
            RAISE EXCEPTION 'المرتجع % آجل وليه قيد قبض نقدي.', v.return_no;
        END IF;
        -- الكمية المرتجعة لكل سطر فاتورة (كل المرتجعات المرحّلة) ≤ اللي اتشرى — بقفل الفواتير الأصلية.
        PERFORM 1 FROM purch.purchase_invoices
          WHERE id IN (SELECT pl.invoice_id FROM purch.purchase_return_lines rl
                         JOIN purch.purchase_invoice_lines pl ON pl.id = rl.source_line_id WHERE rl.return_id = v.id)
          ORDER BY id FOR UPDATE;
        SELECT pl.id, pl.base_qty, i.code, i.name, coalesce(pv.invoice_no, '') AS inv,
               (SELECT coalesce(sum(x.base_qty), 0) FROM purch.purchase_return_lines x
                  JOIN purch.purchase_returns r ON r.id = x.return_id AND r.status = 'posted'
                 WHERE x.source_line_id = pl.id) AS returned INTO o
          FROM purch.purchase_invoice_lines pl
          JOIN purch.purchase_invoices pv ON pv.id = pl.invoice_id
          JOIN inv.items i ON i.id = pl.item_id
         WHERE pl.id IN (SELECT source_line_id FROM purch.purchase_return_lines WHERE return_id = v.id)
           AND (SELECT coalesce(sum(x.base_qty), 0) FROM purch.purchase_return_lines x
                  JOIN purch.purchase_returns r ON r.id = x.return_id AND r.status = 'posted'
                 WHERE x.source_line_id = pl.id) > pl.base_qty
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'الصنف % «%»: المرتجع من فاتورة % (%) أكتر من اللي اتشرى (%).', o.code, o.name, o.inv,
                o.returned, o.base_qty;
        END IF;
    ELSIF nm > 0 OR e.status IS NOT NULL OR p.status IS NOT NULL THEN
        RAISE EXCEPTION 'المرتجع % مسودة وليه حركات مخزون أو قيد — إلغاء الترحيل لازم يمسحهم في نفس العملية.',
            coalesce(v.return_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER purchase_returns_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON purch.purchase_returns
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_returns_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON purch.purchase_returns
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER purchase_returns_post_trg AFTER INSERT OR UPDATE ON purch.purchase_returns
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION purch.purchase_returns_post_check();
CREATE TRIGGER purchase_return_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON purch.purchase_return_lines
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_return_lines_guard();

-- فاتورة الشراء مايتلغيش ترحيلها (ولا تتمسح) وعليها مرتجع (مسودة أو مرحّل) — بالاقتراح.
CREATE FUNCTION purch.purchase_invoices_returns_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    IF OLD.status = 'posted' AND (TG_OP = 'DELETE' OR NEW.status <> 'posted') THEN
        SELECT coalesce(rt.return_no, 'مسودة ' || rt.draft_no) AS label, rt.status INTO r
          FROM purch.purchase_return_lines rl
          JOIN purch.purchase_invoice_lines pl ON pl.id = rl.source_line_id
          JOIN purch.purchase_returns rt ON rt.id = rl.return_id
         WHERE pl.invoice_id = OLD.id
         ORDER BY rt.status DESC, rt.id LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'فاتورة الشراء % عليها مرتجع (% — %) — الغي ترحيل المرتجع وامسحه أو شيل الربط الأول.',
                OLD.invoice_no, r.label, CASE r.status WHEN 'posted' THEN 'مرحّل' ELSE 'مسودة' END;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER purchase_invoices_returns_trg BEFORE UPDATE OR DELETE ON purch.purchase_invoices
    FOR EACH ROW EXECUTE FUNCTION purch.purchase_invoices_returns_guard();

-- ---------------------------------------------------------------------------
-- 6) السنة المالية: نفس الحماية + مرتجعات المشتريات
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
                                    ['purch.purchase_returns', 'return_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير ومرتجعات شراء) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود وفواتير ومرتجعات شراء) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
