-- 0026: المخازن (المرحلة 3د — شاشة «المخازن»، النموذج 2 اختيار المستخدم 2026-11-15).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/10_warehouses.html — ردود المستخدم 2026-11-15):
--   * الشاشة لتعريف المخزن بس — الأرصدة والحركة والتحويل والجرد في المرحلة 6.
--   * **الكود** `WH-001`, `WH-002`… بيتولّد لوحده وثابت (زي TRS / BNK / EXT): المسلسل في `seq`.
--   * **مفيش حساب لكل مخزن**: كل المخازن على حساب مخزون واحد (1105 — «المخزون» في الحسابات الافتراضية)
--     والمخزن «بُعد» على سطر القيد (قرار قديم) · **كل المخازن لكل المستخدمين**.
--   * **أمين المخزن** اختياري — موظف نشط عليه دور «أمين مخزن» (من شاشة الموظفين)، والأمين الواحد يمسك كذا مخزن.
--   * **المخزن الافتراضي**: تشيك بوكس — بيتملى لوحده في الفواتير. واحد بس (unique index)، وتعليمه على مخزن
--     بيشيله من التاني (في الـ Service جوه قفل). الافتراضي مايتوقفش ومايتمسحش.
--   * **مخزن وهمي**: تشيك بوكس «وهمي» زي الأكسس («المخزن الوهمي» QuantityStore4) — حركته كميات بس من غير
--     قيد ولا تكلفة (المرحلة 8)، ومايبقاش الافتراضي.
--   * **مفيش مركز تكلفة** على المخزن (رد المستخدم: «إلا لما نطلبه»).
--   * الأكسس: `tb-Shopes` كانت «أقسام» (= أنواع أصناف — بند لوحده بعدين)، والفواتير كلها على مخزن واحد ←
--     بنبدأ بـ «المخزن الرئيسي» (الافتراضي) + «المخزن الوهمي».
-- لسه (المرحلة 6): المخزن اللي عليه حركة مايتمسحش (FK RESTRICT من دفتر المخزون) · «وهمي» يتثبت بعد أول حركة.

CREATE TABLE inv.warehouses (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq             integer       NOT NULL,
    code            varchar(20)   GENERATED ALWAYS AS
                        ('WH-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar         varchar(120)  NOT NULL,
    name_en         varchar(120),
    is_virtual      boolean       NOT NULL DEFAULT false,               -- وهمي: كميات بس من غير أثر محاسبي
    is_default      boolean       NOT NULL DEFAULT false,               -- بيتملى لوحده في الفواتير (واحد بس)
    keeper_id       integer       REFERENCES party.employees (id) ON DELETE RESTRICT,
    address         varchar(200),
    phone           varchar(30),
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT warehouses_row_version_chk CHECK (row_version > 0),
    CONSTRAINT warehouses_seq_unique UNIQUE (seq),
    CONSTRAINT warehouses_seq_range CHECK (seq > 0),
    CONSTRAINT warehouses_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT warehouses_default_not_virtual CHECK (NOT (is_default AND is_virtual)),
    CONSTRAINT warehouses_default_active CHECK (NOT is_default OR is_active)
);
CREATE UNIQUE INDEX warehouses_code_unique ON inv.warehouses (code);
CREATE UNIQUE INDEX warehouses_name_unique ON inv.warehouses (lower(btrim(name_ar)));
CREATE UNIQUE INDEX warehouses_one_default ON inv.warehouses (is_default) WHERE is_default;
CREATE INDEX warehouses_keeper_idx ON inv.warehouses (keeper_id);
CREATE INDEX warehouses_active_idx ON inv.warehouses (is_active);
COMMENT ON TABLE inv.warehouses IS
    'المخازن: كود WH-001 + أمين (موظف) + افتراضي واحد + وهمي (من غير أثر محاسبي) · '
    'كل المخازن على حساب المخزون الواحد والمخزن بُعد';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.warehouses_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    e  party.employees%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_default THEN
            RAISE EXCEPTION 'المخزن «% %» هو المخزن الافتراضي — علّم مخزن تاني «افتراضي» الأول.', OLD.code, OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود المخزن «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;

    IF NEW.is_default AND NEW.is_virtual THEN
        RAISE EXCEPTION 'المخزن الوهمي «%» مينفعش يبقى المخزن الافتراضي.', NEW.name_ar;
    END IF;
    IF NEW.is_default AND NOT NEW.is_active THEN
        RAISE EXCEPTION 'المخزن «%» هو المخزن الافتراضي — مينفعش يتوقف (علّم مخزن تاني «افتراضي» الأول).', NEW.name_ar;
    END IF;

    -- الأمين: موظف نشط عليه دور «أمين مخزن» (لما يتحدد أو يتغيّر أو المخزن يتنشّط).
    IF NEW.keeper_id IS NOT NULL AND NEW.is_active
       AND (TG_OP = 'INSERT' OR NEW.keeper_id IS DISTINCT FROM OLD.keeper_id OR NOT OLD.is_active) THEN
        SELECT * INTO e FROM party.employees WHERE id = NEW.keeper_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'أمين المخزن مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NOT e.is_storekeeper THEN
            RAISE EXCEPTION 'الموظف «% %» مش عليه دور «أمين مخزن» (فعّله من شاشة الموظفين).', e.code, e.name_ar;
        END IF;
        IF NOT e.is_active THEN
            RAISE EXCEPTION 'الموظف «% %» موقوف — مينفعش يبقى أمين مخزن.', e.code, e.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER warehouses_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON inv.warehouses
    FOR EACH ROW EXECUTE FUNCTION inv.warehouses_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.warehouses
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- حماية أمين المخزن من شاشة الموظفين: مايتمسحش، ومايتشالش منه دور «أمين مخزن» ولا يتوقف وهو أمين على مخزن نشط.
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.employees_warehouse_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg('«' || name_ar || '»', '، ' ORDER BY seq) INTO linked
      FROM inv.warehouses
     WHERE keeper_id = OLD.id AND (TG_OP = 'DELETE' OR is_active);
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على المخزن % — غيّر الأمين من شاشة المخازن الأول.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_storekeeper AND NOT NEW.is_storekeeper THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على المخزن % — غيّر الأمين من شاشة المخازن الأول قبل ما تشيل الدور.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على المخزن % — غيّر الأمين من شاشة المخازن الأول قبل ما توقفه.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER employees_zwarehouse_guard_trg
    BEFORE UPDATE OR DELETE ON party.employees
    FOR EACH ROW EXECUTE FUNCTION party.employees_warehouse_guard();

-- ---------------------------------------------------------------------------
-- المخزنين الأولانيين: «المخزن الرئيسي» (الافتراضي — المخزن الوحيد اللي كان في الأكسس) و«المخزن الوهمي».
-- ---------------------------------------------------------------------------
INSERT INTO inv.warehouses (seq, name_ar, name_en, is_virtual, is_default, notes)
VALUES (1, 'المخزن الرئيسي', 'Main Store', false, true, 'المخزن الوحيد اللي كان في الأكسس'),
       (2, 'المخزن الوهمي', 'Virtual Store', true, false, 'الصرف الوهمي — كميات بس من غير قيد ولا تكلفة');
