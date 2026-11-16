-- 0027: الوحدات والماركات (المرحلة 3د — شاشة «الوحدات والماركات»، النموذج 1 اختيار المستخدم 2026-11-15).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/11_units_brands.html — المستخدم اختار النموذج 1 «تبويبين + فورم وقائمة» من غير
-- رد على الأسئلة الـ 8 ← اتعمل بالاقتراحات، وأي حاجة تتغيّر بسكريبت صغير):
--   * شاشة واحدة بتبويبين (الوحدات · الماركات) وصلاحية واحدة `inv.units_brands`.
--   * **الوحدة**: الرمز (قصير ويظهر في الفاتورة — PCS · M · KG) مطلوب ومايتكررش وبيتخزن حروف كبيرة من غير
--     مسافات · الاسم مطلوب ومايتكررش + EN · النوع (عدد / طول / وزن / مساحة / حجم / وقت) ·
--     **«الكمية تقبل كسور»** (المتر والكيلو آه، العدد لأ — الفاتورة هترفض 1.5 عدد) ·
--     **الوحدة الافتراضية** واحدة بس (بتتملى في الصنف الجديد) ومايتوقفش ولا يتمسح.
--   * **الوحدات المتعددة للصنف** (كرتونة = 12 عدد) بتتعمل على الصنف في شاشة الأصناف (وحدة أساسية + وحدات
--     بمعامل) — هنا أسامي الوحدات بس. المخزون دايماً بالوحدة الأساسية.
--   * **الماركة**: الكود `BRD-001` بيتولّد لوحده وثابت · الاسم مطلوب ومايتكررش **حتى لو بحروف كبيرة/صغيرة
--     أو مسافات** (بيتخزن متنضف — في الأكسس كان فيه HIMEL مرتين وواحدة بمسافة مخفية) · الاسم العربي
--     وبلد المنشأ اختياريين.
--   * الأكسس: الوحدة كانت نص على الصنف (3 وحدات بس بـ 9 كتابات) ← عدد · متر · كيلو جرام + 7 وحدات شائعة.
--     **الماركات الـ 118 مابتتعملش هنا** — بتتنقل مع «نقل بيانات الأكسس» بعد التنضيف ودمج المكرر.
-- لسه (شاشة الأصناف): الوحدة / الماركة اللي عليها أصناف ماتتمسحش (FK RESTRICT) · «تقبل كسور» ماتتقفلش على
-- وحدة عليها كميات بكسور.

-- ---------------------------------------------------------------------------
-- الوحدات
-- ---------------------------------------------------------------------------
CREATE TABLE inv.units (
    id               integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code             varchar(10)   NOT NULL,                             -- الرمز: PCS · M · KG
    name_ar          varchar(60)   NOT NULL,
    name_en          varchar(60),
    category         varchar(10)   NOT NULL DEFAULT 'count',
    allows_fraction  boolean       NOT NULL DEFAULT false,               -- الكمية تقبل كسور
    is_default       boolean       NOT NULL DEFAULT false,               -- بتتملى في الصنف الجديد (واحدة بس)
    notes            varchar(500),
    is_active        boolean       NOT NULL DEFAULT true,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    row_version      integer       NOT NULL DEFAULT 1 CONSTRAINT units_row_version_chk CHECK (row_version > 0),
    CONSTRAINT units_code_format CHECK (code <> '' AND code = upper(code) AND code !~ '\s'),
    CONSTRAINT units_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT units_category_chk CHECK (category IN ('count', 'length', 'weight', 'area', 'volume', 'time')),
    CONSTRAINT units_default_active CHECK (NOT is_default OR is_active)
);
CREATE UNIQUE INDEX units_code_unique ON inv.units (code);
CREATE UNIQUE INDEX units_name_unique ON inv.units (lower(btrim(name_ar)));
CREATE UNIQUE INDEX units_one_default ON inv.units (is_default) WHERE is_default;
CREATE INDEX units_category_idx ON inv.units (category);
CREATE INDEX units_active_idx ON inv.units (is_active);
COMMENT ON TABLE inv.units IS
    'وحدات القياس: رمز + اسم + نوع + «تقبل كسور» + افتراضية واحدة · الوحدات المتعددة للصنف على الصنف نفسه';

CREATE FUNCTION inv.units_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_default THEN
            RAISE EXCEPTION 'الوحدة «% %» هي الوحدة الافتراضية — علّم وحدة تانية «افتراضية» الأول.', OLD.code, OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        NEW.updated_at := now();
    END IF;
    IF NEW.is_default AND NOT NEW.is_active THEN
        RAISE EXCEPTION 'الوحدة «%» هي الوحدة الافتراضية — مينفعش تتوقف (علّم وحدة تانية «افتراضية» الأول).', NEW.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER units_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON inv.units
    FOR EACH ROW EXECUTE FUNCTION inv.units_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.units
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- الوحدات الأولانية: اللي كانت في الأكسس (عدد · متر · كيلو) + الشائعة. «عدد» هي الافتراضية.
INSERT INTO inv.units (code, name_ar, name_en, category, allows_fraction, is_default, notes)
VALUES ('PCS', 'عدد', 'Unit', 'count', false, true, 'في الأكسس: Unit / unit / ك'),
       ('SET', 'طقم', 'Set', 'count', false, false, NULL),
       ('BOX', 'كرتونة', 'Box', 'count', false, false, NULL),
       ('ROLL', 'لفة', 'Roll', 'count', false, false, NULL),
       ('M', 'متر', 'Meter', 'length', true, false, 'في الأكسس: Meter / meter / METER'),
       ('CM', 'سنتيمتر', 'Centimeter', 'length', true, false, NULL),
       ('KG', 'كيلو جرام', 'Kilogram', 'weight', true, false, 'في الأكسس: KGM / كيلو'),
       ('M2', 'متر مربع', 'Square meter', 'area', true, false, NULL),
       ('L', 'لتر', 'Liter', 'volume', true, false, NULL),
       ('HR', 'ساعة', 'Hour', 'time', true, false, 'للخدمات');

-- ---------------------------------------------------------------------------
-- الماركات / الشركات المصنّعة
-- ---------------------------------------------------------------------------
CREATE TABLE inv.item_brands (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq             integer       NOT NULL,
    code            varchar(20)   GENERATED ALWAYS AS
                        ('BRD-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name            varchar(120)  NOT NULL,                               -- الاسم (زي ما بيتكتب على الصنف)
    name_ar         varchar(120),
    country         varchar(60),                                          -- بلد المنشأ
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT item_brands_row_version_chk CHECK (row_version > 0),
    CONSTRAINT item_brands_seq_unique UNIQUE (seq),
    CONSTRAINT item_brands_seq_range CHECK (seq > 0),
    -- الاسم بيتخزن متنضف: من غير مسافات في الأول والآخر ولا مسافتين جنب بعض ولا المسافة المخفية (NBSP).
    CONSTRAINT item_brands_name_clean CHECK (
        name <> '' AND name = btrim(regexp_replace(translate(name, U&'\00A0', ' '), '\s+', ' ', 'g')))
);
CREATE UNIQUE INDEX item_brands_code_unique ON inv.item_brands (code);
CREATE UNIQUE INDEX item_brands_name_unique ON inv.item_brands (lower(name));
CREATE INDEX item_brands_active_idx ON inv.item_brands (is_active);
COMMENT ON TABLE inv.item_brands IS
    'الماركات / الشركات المصنّعة: كود BRD-001 + اسم مايتكررش (من غير حروف كبيرة/صغيرة ولا مسافات) + العربي + البلد';

CREATE FUNCTION inv.item_brands_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.seq <> OLD.seq THEN
        RAISE EXCEPTION 'كود الماركة «%» ثابت — مينفعش يتغيّر.', OLD.code;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER item_brands_guard_trg
    BEFORE UPDATE ON inv.item_brands
    FOR EACH ROW EXECUTE FUNCTION inv.item_brands_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.item_brands
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
