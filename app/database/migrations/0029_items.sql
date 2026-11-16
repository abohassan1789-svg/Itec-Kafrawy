-- 0029: الأصناف (المرحلة 3د — شاشة «الأصناف»، النموذج 3 «كروت أرقام فوق + قائمة وفورم» اختيار المستخدم 2026-11-16).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/13_items.html — ردود المستخدم 2026-11-16):
--   * **الكود** `ITM-0001` بيتولّد لوحده وثابت (المسلسل في `seq`) · **مفيش «رقم كتالوج»** (رد المستخدم: «احذفه»).
--   * **الاسم** مطلوب ومتنضف — عربي أو إنجليزي عادي — و**مايتكررش في نفس النوع** (حتى بحروف كبيرة/صغيرة)؛
--     نفس الاسم في نوع تاني مسموح **برسالة تحذير بس** (التحذير في الشاشة) · «الاسم التاني» اختياري (عربي/إنجليزي).
--   * **النوع** اختياري (من «أنواع الأصناف») · **الحسابات من النوع بس** (مفيش حساب على الصنف — `inv.item_category_account`).
--   * **الوحدة الأساسية** + **وحدات تانية بمعامل** (كرتونة = 12 عدد) في `inv.item_units` — المخزون دايماً بالأساسية.
--   * **خدمي** (من غير مخزون): بياخد «الأصناف الجديدة خدمية» من النوع وبيتغيّر · الخدمي مالوش مخزن افتراضي ولا حد طلب.
--   * **المخزن الافتراضي** اختياري (الفاضي = «المخزن الافتراضي» العام) ومايبقاش وهمي.
--   * **كود الضريبة** مطلوب — الافتراضي ض.ق.م 14% (VAT14) ويتغيّر للصنف (أكواد «إضافة» بس) · **مفيش فاتورة إلكترونية**.
--   * **سعر البيع** و**حد الطلب** اختياريين (السعر بيتملى في الفاتورة ويتغيّر).
--   * الأصناف الـ 3,117 من الأكسس بتتنقل مع «نقل بيانات الأكسس» (المرحلة 11) — مفيش أصناف هنا.
-- لسه (دفتر المخزون — المرحلة 6): الصنف اللي عليه حركة مايتمسحش · الوحدة الأساسية ماتتغيّرش بعد أول حركة ·
--   «خدمي» مايتغيّرش وعليه رصيد · الرصيد وتحت حد الطلب.

CREATE TABLE inv.items (
    id                    integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq                   integer        NOT NULL,
    code                  varchar(20)    GENERATED ALWAYS AS
                              ('ITM-' || CASE WHEN seq < 10000 THEN lpad(seq::text, 4, '0') ELSE seq::text END) STORED,
    name                  varchar(200)   NOT NULL,                       -- 200 = matrialname في الأكسس
    alt_name              varchar(200),                                  -- الاسم التاني (عربي / إنجليزي)
    description           varchar(500),
    category_id           integer        REFERENCES inv.item_categories (id) ON DELETE RESTRICT,
    brand_id              integer        REFERENCES inv.item_brands (id) ON DELETE RESTRICT,
    unit_id               integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,   -- الوحدة الأساسية
    is_service            boolean        NOT NULL DEFAULT false,
    default_warehouse_id  integer        REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    tax_code_id           integer        NOT NULL REFERENCES acc.tax_codes (id) ON DELETE RESTRICT,
    sale_price            numeric(18,4),
    reorder_level         numeric(18,4),
    notes                 varchar(500),
    is_active             boolean        NOT NULL DEFAULT true,
    created_at            timestamptz    NOT NULL DEFAULT now(),
    updated_at            timestamptz    NOT NULL DEFAULT now(),
    row_version           integer        NOT NULL DEFAULT 1 CONSTRAINT items_row_version_chk CHECK (row_version > 0),
    CONSTRAINT items_seq_unique UNIQUE (seq),
    CONSTRAINT items_seq_range CHECK (seq > 0),
    CONSTRAINT items_name_clean CHECK (
        name <> '' AND name = btrim(regexp_replace(translate(name, U&'\00A0', ' '), '\s+', ' ', 'g'))),
    CONSTRAINT items_price_chk CHECK (sale_price IS NULL OR sale_price >= 0),
    CONSTRAINT items_reorder_chk CHECK (reorder_level IS NULL OR reorder_level >= 0),
    CONSTRAINT items_service_chk CHECK (NOT is_service OR (default_warehouse_id IS NULL AND reorder_level IS NULL))
);
CREATE UNIQUE INDEX items_code_unique ON inv.items (code);
-- الاسم مايتكررش في نفس النوع (والأصناف اللي من غير نوع مجموعة واحدة).
CREATE UNIQUE INDEX items_name_unique ON inv.items (coalesce(category_id, 0), lower(name));
CREATE INDEX items_name_idx ON inv.items (lower(name) varchar_pattern_ops);
CREATE INDEX items_category_idx ON inv.items (category_id);
CREATE INDEX items_brand_idx ON inv.items (brand_id);
CREATE INDEX items_unit_idx ON inv.items (unit_id);
CREATE INDEX items_warehouse_idx ON inv.items (default_warehouse_id);
CREATE INDEX items_tax_code_idx ON inv.items (tax_code_id);
CREATE INDEX items_active_idx ON inv.items (is_active);
COMMENT ON TABLE inv.items IS
    'الأصناف: كود ITM-0001 · الاسم مايتكررش في نفس النوع · الحسابات من النوع · وحدة أساسية + وحدات بمعامل (item_units)';

CREATE TABLE inv.item_units (
    id          integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_id     integer        NOT NULL REFERENCES inv.items (id) ON DELETE CASCADE,
    unit_id     integer        NOT NULL REFERENCES inv.units (id) ON DELETE RESTRICT,
    factor      numeric(18,6)  NOT NULL,                                -- الوحدة دي = كام وحدة أساسية
    CONSTRAINT item_units_factor_chk CHECK (factor > 0),
    CONSTRAINT item_units_unique UNIQUE (item_id, unit_id)
);
CREATE INDEX item_units_unit_idx ON inv.item_units (unit_id);
COMMENT ON TABLE inv.item_units IS 'الوحدات التانية للصنف: الوحدة = factor × الوحدة الأساسية (كرتونة = 12 عدد)';

-- ---------------------------------------------------------------------------
-- الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.items_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    c   inv.item_categories%ROWTYPE;
    b   inv.item_brands%ROWTYPE;
    u   inv.units%ROWTYPE;
    w   inv.warehouses%ROWTYPE;
    t   acc.tax_codes%ROWTYPE;
    activating boolean := TG_OP = 'UPDATE' AND NEW.is_active AND NOT OLD.is_active;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود الصنف «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        IF NEW.unit_id <> OLD.unit_id
           AND EXISTS (SELECT 1 FROM inv.item_units WHERE item_id = NEW.id AND unit_id = NEW.unit_id) THEN
            RAISE EXCEPTION 'الوحدة دي موجودة في «الوحدات التانية» للصنف «%» — شيلها من هناك الأول.', NEW.name;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- اللي بيتختار للصنف لازم موجود ونشط (في الإضافة / التغيير / التنشيط).
    IF NEW.category_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.category_id IS DISTINCT FROM OLD.category_id OR activating) THEN
        SELECT * INTO c FROM inv.item_categories WHERE id = NEW.category_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'نوع الصنف مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NEW.is_active AND NOT c.is_active THEN
            RAISE EXCEPTION 'نوع الأصناف «% %» موقوف — اختار نوع نشط.', c.code, c.name;
        END IF;
    END IF;
    IF NEW.brand_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.brand_id IS DISTINCT FROM OLD.brand_id OR activating) THEN
        SELECT * INTO b FROM inv.item_brands WHERE id = NEW.brand_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الماركة مش موجودة (ممكن تكون اتمسحت).';
        END IF;
        IF NEW.is_active AND NOT b.is_active THEN
            RAISE EXCEPTION 'الماركة «%» موقوفة — اختار ماركة نشطة.', b.name;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.unit_id <> OLD.unit_id OR activating THEN
        SELECT * INTO u FROM inv.units WHERE id = NEW.unit_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الوحدة مش موجودة (ممكن تكون اتمسحت).';
        END IF;
        IF NEW.is_active AND NOT u.is_active THEN
            RAISE EXCEPTION 'الوحدة «%» موقوفة — اختار وحدة نشطة.', u.name_ar;
        END IF;
    END IF;
    IF NEW.default_warehouse_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.default_warehouse_id IS DISTINCT FROM OLD.default_warehouse_id OR activating) THEN
        SELECT * INTO w FROM inv.warehouses WHERE id = NEW.default_warehouse_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'المخزن مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF w.is_virtual THEN
            RAISE EXCEPTION 'المخزن «%» وهمي — مايبقاش مخزن افتراضي لصنف.', w.name_ar;
        END IF;
        IF NEW.is_active AND NOT w.is_active THEN
            RAISE EXCEPTION 'المخزن «%» موقوف — اختار مخزن نشط.', w.name_ar;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.tax_code_id <> OLD.tax_code_id OR activating THEN
        SELECT * INTO t FROM acc.tax_codes WHERE id = NEW.tax_code_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'كود الضريبة مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF t.kind <> 'add' THEN
            RAISE EXCEPTION 'كود الضريبة «%» خصم من المنبع — الصنف بياخد قيمة مضافة (أو صفري / معفي).', t.code;
        END IF;
        IF NEW.is_active AND NOT t.is_active THEN
            RAISE EXCEPTION 'كود الضريبة «%» موقوف — اختار كود نشط.', t.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER items_guard_trg
    BEFORE INSERT OR UPDATE ON inv.items
    FOR EACH ROW EXECUTE FUNCTION inv.items_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.items
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

CREATE FUNCTION inv.item_units_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    i   inv.items%ROWTYPE;
    u   inv.units%ROWTYPE;
BEGIN
    SELECT * INTO i FROM inv.items WHERE id = NEW.item_id;
    IF NEW.unit_id = i.unit_id THEN
        RAISE EXCEPTION 'الوحدة التانية للصنف «%» لازم تبقى غير الوحدة الأساسية.', i.name;
    END IF;
    IF TG_OP = 'INSERT' OR NEW.unit_id <> OLD.unit_id THEN
        SELECT * INTO u FROM inv.units WHERE id = NEW.unit_id FOR SHARE;
        IF NOT u.is_active THEN
            RAISE EXCEPTION 'الوحدة «%» موقوفة — اختار وحدة نشطة.', u.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER item_units_guard_trg
    BEFORE INSERT OR UPDATE ON inv.item_units
    FOR EACH ROW EXECUTE FUNCTION inv.item_units_guard();

-- ---------------------------------------------------------------------------
-- الوحدة / الماركة / النوع / المخزن / كود الضريبة اللي عليه أصناف مايتمسحش (رسالة عربي بدل خطأ الـ FK).
-- TG_ARGV[0] = عمود الصنف · TG_ARGV[1] = الاسم اللي في الرسالة.
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.items_master_delete_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n      bigint;
    label  text := coalesce(to_jsonb(OLD) ->> 'name', to_jsonb(OLD) ->> 'name_ar', to_jsonb(OLD) ->> 'code');
BEGIN
    EXECUTE format('SELECT count(*) FROM inv.items WHERE %I = $1', TG_ARGV[0]) INTO n USING OLD.id;
    IF TG_ARGV[0] = 'unit_id' THEN
        n := n + (SELECT count(*) FROM inv.item_units WHERE unit_id = OLD.id);
    END IF;
    IF n > 0 THEN
        RAISE EXCEPTION '% «%» مستخدم في % صنف — اعمله «إيقاف» بدل المسح.', TG_ARGV[1], label, n;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER zitems_delete_guard_trg BEFORE DELETE ON inv.units
    FOR EACH ROW EXECUTE FUNCTION inv.items_master_delete_guard('unit_id', 'الوحدة');
CREATE TRIGGER zitems_delete_guard_trg BEFORE DELETE ON inv.item_brands
    FOR EACH ROW EXECUTE FUNCTION inv.items_master_delete_guard('brand_id', 'الماركة');
CREATE TRIGGER zitems_delete_guard_trg BEFORE DELETE ON inv.item_categories
    FOR EACH ROW EXECUTE FUNCTION inv.items_master_delete_guard('category_id', 'نوع الأصناف');
CREATE TRIGGER zitems_delete_guard_trg BEFORE DELETE ON inv.warehouses
    FOR EACH ROW EXECUTE FUNCTION inv.items_master_delete_guard('default_warehouse_id', 'المخزن');
CREATE TRIGGER zitems_delete_guard_trg BEFORE DELETE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION inv.items_master_delete_guard('tax_code_id', 'كود الضريبة');

-- المخزن اللي افتراضي لأصناف مايتحوّلش وهمي.
CREATE FUNCTION inv.warehouses_items_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    IF NEW.is_virtual AND NOT OLD.is_virtual THEN
        SELECT count(*) INTO n FROM inv.items WHERE default_warehouse_id = OLD.id;
        IF n > 0 THEN
            RAISE EXCEPTION 'المخزن «%» افتراضي لـ % صنف — مايتحوّلش وهمي.', OLD.name_ar, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER zitems_virtual_guard_trg BEFORE UPDATE ON inv.warehouses
    FOR EACH ROW EXECUTE FUNCTION inv.warehouses_items_guard();
