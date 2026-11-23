-- 0045: «بضاعة أول المدة» (المرحلة 6 — النموذج 4 «زي الإكسل»، اختيار المستخدم 2026-11-23 —
--       mockups/phase6/01_opening_stock.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (2026-11-23):
--   * رصيد أول المدة لكل (صنف + مخزن + فاتورة/بيان) **بسعره** (بالوحدة الأساسية) — سطر واحد ومايتكررش.
--     القيمة = الرصيد × السعر هي اللي بيبدأ منها المتوسط المرجّح في دفتر المخزون.
--   * بيتكتب من شاشة «بضاعة أول المدة» (جدول كل الأصناف + الاستيراد من إكسل) **أو** من شاشة الأصناف
--     (الرصيد · السعر · المخزن · فاتورة/بيان) — الاتنين بيعدّلوا نفس السطر.
--   * **السعر صفر مسموح** (تحذير في الشاشة بس) · المخزن الوهمي كمية بس (السعر بيتصفّر) · الصنف الخدمي مالوش رصيد.
--   * **بيترحّل مع «الأرصدة الافتتاحية»** (مفيش ترحيل لوحده): ترحيلها بيدخّل السطور دفتر المخزون (opening_stock)
--     وقيمتها على حساب المخزون (من نوع الصنف ← الافتراضي 1105) والفرق على 34 — وإلغاء الترحيل بيطلّعها.
--     بعد الترحيل السطور مقفولة (زي سطور الأرصدة الافتتاحية نفسها).
--   * رصيد المخزون **مايتكتبش بالإيد** في شاشة الأرصدة الافتتاحية (عشان مايتحسبش مرتين).

-- ---------------------------------------------------------------------------
-- 1) السطور
-- ---------------------------------------------------------------------------
CREATE TABLE inv.opening_stock_lines (
    id            integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id       integer        NOT NULL REFERENCES inv.items (id) ON DELETE CASCADE,
    warehouse_id  integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    stock_kind    varchar(10)    NOT NULL,
    qty           numeric(18,6)  NOT NULL,                       -- بالوحدة الأساسية
    unit_cost     numeric(18,6)  NOT NULL DEFAULT 0,             -- سعر الوحدة الأساسية (صفر مسموح)
    updated_at    timestamptz    NOT NULL DEFAULT now(),
    updated_by    integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    row_version   integer        NOT NULL DEFAULT 1 CONSTRAINT opening_stock_row_version_chk CHECK (row_version > 0),
    CONSTRAINT opening_stock_unique UNIQUE (item_id, warehouse_id, stock_kind),
    CONSTRAINT opening_stock_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT opening_stock_qty_chk CHECK (qty > 0),
    CONSTRAINT opening_stock_cost_chk CHECK (unit_cost >= 0)
);
CREATE INDEX opening_stock_warehouse_idx ON inv.opening_stock_lines (warehouse_id, stock_kind);
CREATE INDEX opening_stock_updated_by_idx ON inv.opening_stock_lines (updated_by) WHERE updated_by IS NOT NULL;
COMMENT ON TABLE inv.opening_stock_lines IS
    'بضاعة أول المدة: (صنف + مخزن + فاتورة/بيان) بالرصيد والسعر — بتترحّل مع الأرصدة الافتتاحية لدفتر المخزون';

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.opening_stock_lines
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version('updated_by');

CREATE FUNCTION inv.opening_stock_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h   acc.opening_balances%ROWTYPE;
    it  inv.items%ROWTYPE;
    wh  inv.warehouses%ROWTYPE;
BEGIN
    -- FOR SHARE: محدش يرحّل الأرصدة الافتتاحية واحنا بنعدّل السطور.
    SELECT * INTO h FROM acc.opening_balances WHERE id = 1 FOR SHARE;
    IF h.status = 'posted' THEN
        RAISE EXCEPTION 'مستند الأرصدة الافتتاحية مرحّل — بضاعة أول المدة مابتتعدّلش (الغي ترحيله الأول).';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.item_id, NEW.warehouse_id, NEW.stock_kind)
                            IS DISTINCT FROM (OLD.item_id, OLD.warehouse_id, OLD.stock_kind) THEN
        RAISE EXCEPTION 'الصنف والمخزن والنوع ثابتين في السطر — امسحه واعمل سطر جديد.';
    END IF;
    SELECT * INTO it FROM inv.items WHERE id = NEW.item_id;
    IF it.is_service THEN
        RAISE EXCEPTION 'الصنف % «%» خدمي — مالوش رصيد أول المدة.', 'ITM-' || lpad(it.seq::text, 4, '0'), it.name;
    END IF;
    SELECT * INTO wh FROM inv.warehouses WHERE id = NEW.warehouse_id;
    IF wh.is_virtual THEN
        NEW.unit_cost := 0;                     -- الوهمي كمية بس
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER opening_stock_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON inv.opening_stock_lines
    FOR EACH ROW EXECUTE FUNCTION inv.opening_stock_guard();

-- الصنف اللي عليه رصيد أول المدة مايتحوّلش خدمي.
CREATE FUNCTION inv.items_opening_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_service AND NOT OLD.is_service
       AND EXISTS (SELECT 1 FROM inv.opening_stock_lines WHERE item_id = OLD.id) THEN
        RAISE EXCEPTION 'الصنف % «%» عليه رصيد أول المدة — مايتحوّلش خدمي (امسح الرصيد الأول).',
            'ITM-' || lpad(OLD.seq::text, 4, '0'), OLD.name;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER items_opening_guard_trg
    BEFORE UPDATE OF is_service ON inv.items
    FOR EACH ROW EXECUTE FUNCTION inv.items_opening_guard();

-- ---------------------------------------------------------------------------
-- 2) الأرصدة الافتتاحية: قيمة البضاعة على حساب المخزون (من نوع الصنف ← الافتراضي) مع الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind,
                               sort_order)
VALUES ('opening_balance', 'inventory', 'بضاعة أول المدة', 'dr', 'category', NULL, 'inventory', NULL, 3);

-- رصيد المخزون مايتكتبش بالإيد في سطور الأرصدة الافتتاحية (بيتحسب من «بضاعة أول المدة»).
CREATE FUNCTION acc.opening_lines_inventory_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a acc.accounts%ROWTYPE;
BEGIN
    IF coalesce(current_setting('acc.opening_line_move', true), '') = 'on' THEN
        RETURN NEW;                              -- التفريع بس
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.account_id = OLD.account_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id;
    IF a.role_code = 'inventory' THEN
        RAISE EXCEPTION 'الحساب «% %» حساب مخزون — رصيده بيتحسب لوحده من شاشة «بضاعة أول المدة» (أو رصيد أول المدة في شاشة الأصناف).',
            a.code, a.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER opening_lines_inventory_guard_trg
    BEFORE INSERT OR UPDATE ON acc.opening_balance_lines
    FOR EACH ROW EXECUTE FUNCTION acc.opening_lines_inventory_guard();
