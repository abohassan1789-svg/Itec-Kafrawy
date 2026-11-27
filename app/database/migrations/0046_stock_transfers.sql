-- 0046: «التحويل بين المخازن» (المرحلة 6 — النموذج 8 «لوحة المراجعة قبل الترحيل»، اختيار المستخدم 2026-11-27
--       — mockups/phase6/02_stock_transfer.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (المستخدم اختار النموذج 8، والأسئلة اللي مااتردش عليها اتعملت بالاقتراح):
--   * **خطوة واحدة**: السطر بيطلع من مخزن «من» بالمتوسط المرجّح ويدخل مخزن «إلى» بنفس القيمة (دفتر المخزون 0043).
--   * «من مخزن / إلى مخزن» و«من نوع / إلى نوع» (فاتورة / بيان) **في الرأس** للتحويل كله · نفس المخزن مسموح لو النوع
--     اتغيّر (فاتورة ↔ بيان) · نفس المخزن ونفس النوع ممنوع.
--   * **مسودة ← ترحيل** والرقم TR-2026-00001 من أول ترحيل (ويبدأ من 1 كل سنة) · «إلغاء الترحيل» بالصلاحية في السنة
--     المفتوحة والرقم ثابت · المسح للمسودة اللي ماخدتش رقم بس.
--   * الكمية **بوحدة من وحدات الصنف** (الأساسية أو التانية بمعاملها — المعامل بيتحفظ مع السطر) · نفس الصنف مرة واحدة
--     في التحويل (الـ Service بيجمّعه) · السعر مابيتكتبش (المتوسط لوحده) · التاريخ القديم مسموح (المحرك بيعيد الحساب).
--   * الرصيد مش مكفي ← ممنوع إلا بصلاحية «من غير رصيد» للشاشة دي (المحرك) · فاتورة ↔ بيان مايعدّيش رصيد النوع
--     في الشركة حتى بالصلاحية (المحرك).
--   * القيد: بين مخزنين حقيقيين **مفيش قيد** (كل المخازن على حساب المخزون والمخزن بُعد) · **حقيقي ← وهمي**: مدين
--     «بضاعة المخزن الوهمي» (حساب جديد تحت 52 + مفتاح افتراضي) / دائن المخزون (من نوع الصنف) بقيمة الخارج ·
--     **وهمي ← حقيقي**: العكس بقيمة الداخل (آخر متوسط) — نفس القواعد بمبلغ سالب.
-- الحماية في القاعدة نفسها (زي حركات الخزائن):
--   * السنة من التاريخ ولازم مفتوحة · المرحّل مايتعدّلش ولا يتمسح ولا سطوره · الرقم ثابت وفي نفس السنة.
--   * المخزنين موجودين ونشطين وقت الإضافة أو التغيير أو الترحيل · الصنف نشط ومش خدمي · الوحدة من وحدات الصنف.
--   * **المرحّل لازم ليه حركتين في دفتر المخزون لكل سطر، وقيد مرحّل بقيمتها لو فيه وهمي · والمسودة مالهاش ولا ده
--     ولا ده** (Constraint Trigger مؤجل).
--   * السنة ماتتقفلش وفيها تحويلات مسودة، وتواريخها ماتسيبش تحويل برّاها.

-- ---------------------------------------------------------------------------
-- 1) حساب «بضاعة المخزن الوهمي» + مفتاحه في الحسابات الافتراضية
-- ---------------------------------------------------------------------------
INSERT INTO acc.account_roles (code, name_ar, name_en, account_type, default_party_kind, sort_order)
VALUES ('virtual_stock', 'بضاعة المخزن الوهمي', 'Virtual Warehouse Stock', 'expense', NULL,
        (SELECT max(sort_order) + 1 FROM acc.account_roles));

-- الكود الجاي تحت 52 (أكبر كود من 4 أرقام + 1) — بيتضاف بس لو 52 رئيسي ونشط وفيه مكان.
DO $$
DECLARE
    parent  acc.accounts%ROWTYPE;
    nxt     integer;
BEGIN
    SELECT * INTO parent FROM acc.accounts WHERE code = '52';
    IF NOT FOUND OR parent.is_leaf OR NOT parent.is_active THEN
        RETURN;
    END IF;
    SELECT coalesce(max(right(code, 2)::integer), 0) + 1 INTO nxt
      FROM acc.accounts WHERE parent_id = parent.id AND code ~ '^52[0-9]{2}$';
    IF nxt > 99 THEN
        RETURN;
    END IF;
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, is_system, is_active)
    VALUES ('52' || lpad(nxt::text, 2, '0'), 'بضاعة المخزن الوهمي', 'Virtual Warehouse Stock', parent.id, true,
            'expense', 'dr', 'operating_exp', 'op', 'virtual_stock', true, true);
END $$;

ALTER TABLE acc.default_accounts DISABLE TRIGGER zz_row_version_trg;
UPDATE acc.default_accounts SET sort_order = sort_order + 1
 WHERE sort_order > (SELECT sort_order FROM acc.default_accounts WHERE key = 'cost_var');
INSERT INTO acc.default_accounts (key, group_code, name_ar, account_type, role_code, used_in, sort_order, account_id)
VALUES ('virtual_stock', 'inv', 'بضاعة المخزن الوهمي', 'expense', 'virtual_stock',
        'تحويل للمخزن الوهمي / منه',
        (SELECT sort_order + 1 FROM acc.default_accounts WHERE key = 'cost_var'),
        (SELECT id FROM acc.accounts WHERE role_code = 'virtual_stock' AND is_leaf AND is_active ORDER BY code LIMIT 1));
ALTER TABLE acc.default_accounts ENABLE TRIGGER zz_row_version_trg;

-- ---------------------------------------------------------------------------
-- 2) الترقيم + نوع المستند + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('stock_transfer', 'تحويل بين المخازن', 'TR', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('stock_transfer', 'تحويل بين المخازن', (SELECT max(sort_order) + 1 FROM acc.posting_doc_types));

-- حقيقي ← وهمي بمبلغ موجب · وهمي ← حقيقي بنفس القواعد بمبلغ سالب (الجنب بيتقلب).
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order) VALUES
    ('stock_transfer', 'virtual',   'بضاعة المخزن الوهمي', 'dr', 'default',  'virtual_stock', NULL,        NULL, 1),
    ('stock_transfer', 'inventory', 'المخزون',             'cr', 'category', NULL,            'inventory', NULL, 2);

-- ---------------------------------------------------------------------------
-- 3) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE inv.stock_transfers_draft_seq AS integer;
CREATE TABLE inv.stock_transfers (
    id                 integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_no        varchar(30),                                   -- TR-2026-00001 (من أول ترحيل)
    draft_no           integer        NOT NULL DEFAULT nextval('inv.stock_transfers_draft_seq'),
    transfer_date      date           NOT NULL,
    fiscal_year_id     integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    from_warehouse_id  integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    to_warehouse_id    integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    from_kind          varchar(10)    NOT NULL DEFAULT 'invoice',      -- invoice فاتورة · statement بيان
    to_kind            varchar(10)    NOT NULL DEFAULT 'invoice',
    description        varchar(500),                                   -- ملاحظات (اختياري)
    status             varchar(10)    NOT NULL DEFAULT 'draft',
    created_by         integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at          timestamptz,
    posted_by          integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at         timestamptz    NOT NULL DEFAULT now(),
    updated_at         timestamptz    NOT NULL DEFAULT now(),
    row_version        integer        NOT NULL DEFAULT 1 CONSTRAINT stock_transfers_row_version_chk CHECK (row_version > 0),
    CONSTRAINT stock_transfers_no_unique UNIQUE (transfer_no),
    CONSTRAINT stock_transfers_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT stock_transfers_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT stock_transfers_kinds_chk CHECK (from_kind IN ('invoice', 'statement') AND to_kind IN ('invoice', 'statement')),
    CONSTRAINT stock_transfers_same_chk CHECK (from_warehouse_id <> to_warehouse_id OR from_kind <> to_kind),
    CONSTRAINT stock_transfers_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT stock_transfers_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR transfer_no IS NOT NULL))
);
ALTER SEQUENCE inv.stock_transfers_draft_seq OWNED BY inv.stock_transfers.draft_no;
COMMENT ON TABLE inv.stock_transfers IS
    'التحويل بين المخازن (خطوة واحدة): من مخزن + نوع ← إلى مخزن + نوع · مسودة ← مرحّل بحركات دفتر المخزون (وقيد لو فيه وهمي)';

CREATE TABLE inv.stock_transfer_lines (
    id           integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_id  integer        NOT NULL REFERENCES inv.stock_transfers (id) ON DELETE CASCADE,
    line_no      smallint       NOT NULL,
    item_id      integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    unit_id      integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,
    factor       numeric(18,6)  NOT NULL DEFAULT 1,                -- الوحدة = كام وحدة أساسية (وقت الكتابة)
    qty          numeric(18,6)  NOT NULL,                          -- بالوحدة المختارة
    base_qty     numeric(18,6)  GENERATED ALWAYS AS (round(qty * factor, 6)) STORED,
    CONSTRAINT stock_transfer_lines_no_unique UNIQUE (transfer_id, line_no),
    CONSTRAINT stock_transfer_lines_item_unique UNIQUE (transfer_id, item_id),
    CONSTRAINT stock_transfer_lines_line_chk CHECK (line_no > 0),
    CONSTRAINT stock_transfer_lines_factor_chk CHECK (factor > 0),
    CONSTRAINT stock_transfer_lines_qty_chk CHECK (qty > 0 AND round(qty * factor, 6) > 0)
);
COMMENT ON TABLE inv.stock_transfer_lines IS 'أصناف التحويل: الكمية بالوحدة المختارة × المعامل = الكمية بالوحدة الأساسية';

-- القائمة بالفترة (الأحدث فوق) + index على كل FK وعلى البحث.
CREATE INDEX stock_transfers_date_idx ON inv.stock_transfers (transfer_date DESC, id DESC);
CREATE INDEX stock_transfers_year_idx ON inv.stock_transfers (fiscal_year_id, status);
CREATE INDEX stock_transfers_from_idx ON inv.stock_transfers (from_warehouse_id, transfer_date DESC);
CREATE INDEX stock_transfers_to_idx ON inv.stock_transfers (to_warehouse_id, transfer_date DESC);
CREATE INDEX stock_transfers_created_by_idx ON inv.stock_transfers (created_by);
CREATE INDEX stock_transfers_posted_by_idx ON inv.stock_transfers (posted_by);
CREATE INDEX stock_transfers_desc_idx ON inv.stock_transfers (lower(description) varchar_pattern_ops)
    WHERE description IS NOT NULL;
CREATE INDEX stock_transfer_lines_item_idx ON inv.stock_transfer_lines (item_id);
CREATE INDEX stock_transfer_lines_unit_idx ON inv.stock_transfer_lines (unit_id);

-- ---------------------------------------------------------------------------
-- 4) الحماية
-- ---------------------------------------------------------------------------
-- المخزن: موجود ونشط.
CREATE FUNCTION inv.stock_transfer_warehouse_check(p_warehouse integer, p_side text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    w  record;
BEGIN
    SELECT code, name_ar, is_active INTO w FROM inv.warehouses WHERE id = p_warehouse FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مخزن «%» مش موجود (ممكن يكون اتمسح).', p_side;
    END IF;
    IF NOT w.is_active THEN
        RAISE EXCEPTION 'المخزن «% %» (%) موقوف — اختار مخزن نشط.', w.code, w.name_ar, p_side;
    END IF;
END $$;

CREATE FUNCTION inv.stock_transfers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'التحويل ' || coalesce(OLD.transfer_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — تحويلاتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.transfer_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    -- السنة المالية من التاريخ (لازم مفتوحة).
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.transfer_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.transfer_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها تحويل.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.transfer_no IS NOT NULL THEN
            RAISE EXCEPTION 'التحويل الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.transfer_no IS NOT NULL AND NEW.transfer_no IS DISTINCT FROM OLD.transfer_no) THEN
            RAISE EXCEPTION 'رقم التحويل ثابت.';
        END IF;
        IF OLD.transfer_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
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
            -- إلغاء الترحيل: الحالة بس (التحويل بيرجع مسودة برقمه).
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع التحويل مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NOT EXISTS (SELECT 1 FROM inv.stock_transfer_lines WHERE transfer_id = NEW.id) THEN
                RAISE EXCEPTION '% مافيهوش أصناف — ضيف صنف واحد على الأقل قبل الترحيل.', lbl;
            END IF;
            IF NEW.transfer_no IS NULL THEN
                NEW.transfer_no := core.next_document_no('stock_transfer', NEW.transfer_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- المخزنين: وقت الإضافة أو التغيير أو الترحيل.
    IF TG_OP = 'INSERT' OR posting OR NEW.from_warehouse_id IS DISTINCT FROM OLD.from_warehouse_id THEN
        PERFORM inv.stock_transfer_warehouse_check(NEW.from_warehouse_id, 'من');
    END IF;
    IF TG_OP = 'INSERT' OR posting OR NEW.to_warehouse_id IS DISTINCT FROM OLD.to_warehouse_id THEN
        PERFORM inv.stock_transfer_warehouse_check(NEW.to_warehouse_id, 'إلى');
    END IF;
    RETURN NEW;
END $$;

-- السطور: التحويل لازم مسودة · الصنف نشط ومش خدمي · الوحدة من وحدات الصنف بمعاملها.
CREATE FUNCTION inv.stock_transfer_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h    record;
    it   record;
    f    numeric;
BEGIN
    SELECT status, transfer_no, draft_no INTO h FROM inv.stock_transfers
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.transfer_id ELSE NEW.transfer_id END;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- التحويل نفسه اتمسح (CASCADE)
    END IF;
    IF h.status <> 'draft' THEN
        RAISE EXCEPTION 'التحويل % مرحّل — أصنافه ماتتعدّلش (الغي ترحيله الأول).',
            coalesce(h.transfer_no, 'مسودة ' || h.draft_no);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.transfer_id <> OLD.transfer_id THEN
        RAISE EXCEPTION 'السطر مايتنقلش لتحويل تاني.';
    END IF;
    IF TG_OP = 'INSERT' OR NEW.item_id <> OLD.item_id OR NEW.unit_id <> OLD.unit_id OR NEW.factor <> OLD.factor THEN
        SELECT code, name, is_service, is_active, unit_id INTO it FROM inv.items WHERE id = NEW.item_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الصنف مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF it.is_service THEN
            RAISE EXCEPTION 'الصنف % «%» خدمي — مالوش رصيد يتحوّل.', it.code, it.name;
        END IF;
        IF NOT it.is_active AND (TG_OP = 'INSERT' OR NEW.item_id <> OLD.item_id) THEN
            RAISE EXCEPTION 'الصنف % «%» موقوف — مايتحوّلش.', it.code, it.name;
        END IF;
        IF NEW.unit_id = it.unit_id THEN
            f := 1;
        ELSE
            SELECT factor INTO f FROM inv.item_units WHERE item_id = NEW.item_id AND unit_id = NEW.unit_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'الوحدة دي مش من وحدات الصنف % «%».', it.code, it.name;
            END IF;
        END IF;
        IF NEW.factor <> f THEN
            RAISE EXCEPTION 'معامل الوحدة للصنف % «%» اتغيّر — افتح التحويل من جديد.', it.code, it.name;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- المرحّل: حركتين في دفتر المخزون لكل سطر + قيد مرحّل بقيمتها لو فيه وهمي · المسودة مالهاش (آخر الـ transaction).
CREATE FUNCTION inv.stock_transfers_post_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    nl     integer;
    nm     integer;
    total  numeric;
BEGIN
    SELECT id, status, transfer_no, draft_no INTO v FROM inv.stock_transfers WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    SELECT count(*) INTO nl FROM inv.stock_transfer_lines WHERE transfer_id = v.id;
    SELECT count(*), abs(coalesce(sum(booked_value) FILTER (WHERE NOT is_virtual), 0)) INTO nm, total
      FROM inv.stock_movements WHERE source_type = 'transfer' AND source_id = v.id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'stock_transfer' AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF nm <> 2 * nl OR nl = 0 THEN
            RAISE EXCEPTION 'التحويل % مرحّل من غير حركات المخزون بتاعته — الترحيل لازم يعملها في نفس العملية.',
                v.transfer_no;
        END IF;
        IF total <> 0 AND e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'التحويل % (للمخزن الوهمي / منه) مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.',
                v.transfer_no;
        END IF;
        IF e.status IS NOT NULL AND e.total_debit <> total THEN
            RAISE EXCEPTION 'قيد التحويل % مبلغه % مش زي قيمة الحركات %.', v.transfer_no, e.total_debit, total;
        END IF;
    ELSIF nm > 0 OR e.status IS NOT NULL THEN
        RAISE EXCEPTION 'التحويل % مسودة وليه حركات مخزون أو قيد — إلغاء الترحيل لازم يمسحهم في نفس العملية.',
            coalesce(v.transfer_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stock_transfers_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON inv.stock_transfers
    FOR EACH ROW EXECUTE FUNCTION inv.stock_transfers_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.stock_transfers
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER stock_transfers_post_trg AFTER INSERT OR UPDATE ON inv.stock_transfers
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inv.stock_transfers_post_check();
CREATE TRIGGER stock_transfer_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON inv.stock_transfer_lines
    FOR EACH ROW EXECUTE FUNCTION inv.stock_transfer_lines_guard();

-- ---------------------------------------------------------------------------
-- 5) السنة المالية: نفس حماية السندات + التحويلات (كل جدول بعمود تاريخه)
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
                                    ['acc.profit_distributions', 'dist_date'], ['inv.stock_transfers', 'transfer_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
