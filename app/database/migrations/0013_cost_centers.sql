-- 0013: مراكز التكلفة (المرحلة 2 — شاشة «مراكز التكلفة»، النموذج 1 «تبويب لكل مركز رئيسي + شجرة + فورم»).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- الفكرة (docs/03 القسم 3.10 والفكرة 12 + mockups/phase2/03_cost_centers.html + قرارات المستخدم 2026-11-09):
--   * مركز التكلفة = «الفلوس دي لمين جوه الشركة» (BMS، ورشة MCC، التركيبات، الإدارة…) — بُعد على سطر القيد
--     جنب الحساب (الحساب = «إيه»، المركز = «لمين»).
--   * شجرة زي الدليل بالظبط: الكود أرقام بس، **بيبدأ بكود الأب وأطول منه** (2 ← 22 ← 2201) لحد 5 مستويات ·
--     رئيسي (تجميعي) / فرعي (هو اللي بيتكتب في القيد).
--   * النوع (إداري / تشغيلي / بيعي / خدمي) — اختيار المستخدم: يتعمل (بيتقترح من الأب ويتغيّر).
--   * **فلتر الأب بيجيب الأبناء**: acc.cost_center_subtree(id) (Recursive CTE) — أي تقرير يختار «2 الأنشطة الفنية»
--     بيجمع كل اللي تحته.
--   * التفريع زي الدليل (acc.split_cost_center): الفرعي يتحوّل رئيسي و«<الاسم> — عام» تحته بياخد حركته.
--   * المسح: اللي تحته مراكز مايتمسحش (رسالة عربي) — وأوامر التوريد والقيود هيمنعوه بـ FK RESTRICT لما يتعملوا.
-- لسه (مع القيود — المرحلة 4): الحسابات اللي «لازم مركز تكلفة» (acc.accounts.requires_cost_center) بترفض السطر
-- من غير مركز فرعي نشط · منع تحويل مركز عليه قيود لرئيسي من «تعديل» · «المركز الافتراضي / المسموح» لكل حساب
-- (فكرة النموذج 8 — اختيارية).

CREATE TABLE acc.cost_centers (
    id            integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          varchar(20)  NOT NULL,
    name_ar       varchar(120) NOT NULL,
    name_en       varchar(120),
    parent_id     integer      REFERENCES acc.cost_centers (id) ON DELETE RESTRICT,
    level         smallint     NOT NULL DEFAULT 1,              -- بيتحسب (trigger)
    is_leaf       boolean      NOT NULL DEFAULT true,           -- فرعي = بيتكتب في القيد · رئيسي = تجميعي
    center_type   varchar(10)  NOT NULL DEFAULT 'admin',        -- admin إداري · ops تشغيلي · sales بيعي · serv خدمي
    manager_name  varchar(120),                                 -- المسؤول (نص — الموظفين بيتربطوا بعدين)
    notes         varchar(500),
    is_active     boolean      NOT NULL DEFAULT true,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    row_version   integer      NOT NULL DEFAULT 1 CONSTRAINT cost_centers_row_version_chk CHECK (row_version > 0),
    CONSTRAINT cost_centers_code_unique UNIQUE (code),
    CONSTRAINT cost_centers_code_format CHECK (code ~ '^[0-9]{1,20}$'),
    CONSTRAINT cost_centers_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT cost_centers_level_range CHECK (level BETWEEN 1 AND 5),
    CONSTRAINT cost_centers_type_chk CHECK (center_type IN ('admin', 'ops', 'sales', 'serv')),
    CONSTRAINT cost_centers_root_chk CHECK ((parent_id IS NULL) = (level = 1))
);
CREATE INDEX cost_centers_parent_idx ON acc.cost_centers (parent_id);
CREATE INDEX cost_centers_name_idx ON acc.cost_centers (name_ar);
CREATE INDEX cost_centers_type_idx ON acc.cost_centers (center_type, code);
COMMENT ON TABLE acc.cost_centers IS 'مراكز التكلفة (شجرة) — القيد على المركز الفرعي النشط بس، والفلتر على الأب بيجيب كل اللي تحته';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل) — نفس قواعد الدليل (0009 / 0011) من غير النظامي والدور
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.cost_centers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p         acc.cost_centers%ROWTYPE;
    has_kids  boolean := false;
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.level := 1;
    ELSE
        -- FOR SHARE: لو جهاز تاني بيحوّل الأب فرعي أو بيوقفه في نفس اللحظة، واحد يستنى التاني.
        SELECT * INTO p FROM acc.cost_centers WHERE id = NEW.parent_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'مركز التكلفة الأب مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF p.id = NEW.id THEN
            RAISE EXCEPTION 'المركز مينفعش يبقى تحت نفسه.';
        END IF;
        IF p.is_leaf THEN
            RAISE EXCEPTION 'المركز «% %» فرعي — مينفعش يبقى تحته مراكز (فرّع منه بـ «+ فرعي تحت المحدد»).',
                p.code, p.name_ar;
        END IF;
        IF NOT p.is_active AND (TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                                OR (NEW.is_active AND NOT OLD.is_active)) THEN
            RAISE EXCEPTION 'المركز «% %» موقوف — مينفعش يبقى تحته مراكز نشطة أو جديدة.', p.code, p.name_ar;
        END IF;
        IF left(NEW.code, length(p.code)) <> p.code OR length(NEW.code) <= length(p.code) THEN
            RAISE EXCEPTION 'كود المركز لازم يبدأ بكود الأب «%» ويبقى أطول منه.', p.code;
        END IF;
        NEW.level := p.level + 1;
        IF NEW.level > 5 THEN
            RAISE EXCEPTION 'أقصى عدد مستويات لمراكز التكلفة 5 — مينفعش تضيف تحت «%».', p.code;
        END IF;
    END IF;
    IF NEW.level = 5 AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'المستوى الخامس آخر مستوى — المركز «%» لازم يبقى فرعي.', NEW.code;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        has_kids := EXISTS (SELECT 1 FROM acc.cost_centers WHERE parent_id = OLD.id);
        IF has_kids AND (NEW.code <> OLD.code OR NEW.parent_id IS DISTINCT FROM OLD.parent_id) THEN
            RAISE EXCEPTION 'المركز «% %» تحته مراكز — مينفعش تغيّر كوده ولا مكانه.', OLD.code, OLD.name_ar;
        END IF;
        IF has_kids AND NEW.is_leaf THEN
            RAISE EXCEPTION 'المركز «% %» تحته مراكز — مينفعش يتحوّل فرعي.', OLD.code, OLD.name_ar;
        END IF;
        IF OLD.is_active AND NOT NEW.is_active
           AND EXISTS (SELECT 1 FROM acc.cost_centers WHERE parent_id = OLD.id AND is_active) THEN
            RAISE EXCEPTION 'المركز «% %» تحته مراكز نشطة — وقّفهم الأول.', OLD.code, OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER cost_centers_guard_trg
    BEFORE INSERT OR UPDATE ON acc.cost_centers
    FOR EACH ROW EXECUTE FUNCTION acc.cost_centers_guard();

-- الحماية (مسح): اللي تحته مراكز مايتمسحش (رسالة عربي بدل خطأ الـ FK).
CREATE FUNCTION acc.cost_centers_delete_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM acc.cost_centers WHERE parent_id = OLD.id) THEN
        RAISE EXCEPTION 'المركز «% %» تحته مراكز — امسحهم الأول.', OLD.code, OLD.name_ar;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER cost_centers_delete_guard_trg
    BEFORE DELETE ON acc.cost_centers
    FOR EACH ROW EXECUTE FUNCTION acc.cost_centers_delete_guard();

-- حماية التعديل في نفس الوقت (المرحلة 1ج — زي 0008).
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.cost_centers
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- فلتر الأب بيجيب الأبناء (المركز نفسه + كل اللي تحته بأي عمق)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.cost_center_subtree(p_center_id integer)
RETURNS TABLE (id integer)
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE tree AS (
        SELECT c.id FROM acc.cost_centers c WHERE c.id = p_center_id
        UNION ALL
        SELECT c.id FROM acc.cost_centers c JOIN tree t ON c.parent_id = t.id
    )
    SELECT tree.id FROM tree
$$;
COMMENT ON FUNCTION acc.cost_center_subtree(integer) IS
    'المركز + كل اللي تحته (Recursive CTE) — للتقارير: «WHERE cost_center_id IN (SELECT id FROM acc.cost_center_subtree(…))»';

-- ---------------------------------------------------------------------------
-- تفريع مركز فرعي (زي acc.split_account في 0011)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.split_cost_center(p_center_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم المركز «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    p          acc.cost_centers%ROWTYPE;
    v_general  integer;
    v_code     varchar(20);
    v_suffix   constant text := ' — عام';
BEGIN
    -- 1) القفل: أي جهاز تاني بيعدّل / بيفرّع المركز ده بيستنى لحد ما نخلص.
    SELECT * INTO p FROM acc.cost_centers WHERE id = p_center_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مركز التكلفة ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT p.is_leaf THEN
        RAISE EXCEPTION 'المركز «% %» رئيسي أصلاً — ضيف تحته على طول.', p.code, p.name_ar;
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'المركز «% %» في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.', p.code, p.name_ar;
    END IF;
    IF NOT p.is_active THEN
        RAISE EXCEPTION 'المركز «% %» موقوف — نشّطه الأول وبعدين فرّع منه.', p.code, p.name_ar;
    END IF;

    -- 2) المركز يتحوّل رئيسي.
    UPDATE acc.cost_centers SET is_leaf = false WHERE id = p.id;

    -- 3) أول مركز تحته «— عام» (مع كل تفريع — نفس قرار الدليل) بنفس النوع والمسؤول.
    v_code := coalesce(nullif(btrim(p_general_code), ''),
                       p.code || CASE WHEN p.level <= 1 THEN '1' ELSE '01' END);
    INSERT INTO acc.cost_centers (code, name_ar, name_en, parent_id, is_leaf, center_type, manager_name, is_active)
    VALUES (v_code, left(p.name_ar, 120 - length(v_suffix)) || v_suffix,
            CASE WHEN p.name_en IS NULL THEN NULL ELSE left(p.name_en, 110) || ' — General' END,
            p.id, true, p.center_type, p.manager_name, true)
    RETURNING id INTO v_general;

    -- 4) TODO (أوامر التوريد — البند الجاي): UPDATE acc.projects SET cost_center_id = v_general WHERE cost_center_id = p.id;
    --    TODO (القيود — المرحلة 4): UPDATE acc.journal_lines SET cost_center_id = v_general WHERE cost_center_id = p.id;
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_cost_center(integer, varchar) IS
    'تفريع مركز فرعي: يتحوّل رئيسي + «<الاسم> — عام» تحته (وأوامر التوريد والقيود بتتنقل له لما يتعملوا)';

-- ---------------------------------------------------------------------------
-- المراكز المبدئية (أمثلة النماذج لشركة ITEC — اختيار المستخدم 2026-11-09، وبتتعدّل من الشاشة)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('1',    'الإدارة العامة',              'General Management',    NULL,  false, 'admin'),
            ('11',   'الإدارة العليا',              'Top Management',        '1',   true,  'admin'),
            ('12',   'الحسابات والمالية',           'Finance',               '1',   true,  'admin'),
            ('13',   'الموارد البشرية',             'HR',                    '1',   true,  'admin'),
            ('14',   'المكتب (Office)',             'Office',                '1',   true,  'admin'),
            ('2',    'الأنشطة الفنية',              'Technical Operations',  NULL,  false, 'ops'),
            ('21',   'BMS — أنظمة إدارة المباني',   'BMS',                   '2',   false, 'ops'),
            ('2101', 'BMS — مشروعات',               'BMS Projects',          '21',  true,  'ops'),
            ('2102', 'BMS — عقود صيانة',            'BMS Maintenance',       '21',  true,  'ops'),
            ('22',   'MCC — لوحات التحكم',          'MCC',                   '2',   false, 'ops'),
            ('2201', 'MCC — ورشة التصنيع',          'MCC Workshop',          '22',  true,  'ops'),
            ('2202', 'MCC — توريد',                 'MCC Supply',            '22',  true,  'ops'),
            ('23',   'التركيبات (Installation)',    'Installation',          '2',   true,  'ops'),
            ('24',   'الصيانة وخدمة ما بعد البيع',  'After-Sales Service',   '2',   true,  'ops'),
            ('3',    'المبيعات والتسويق',           'Sales & Marketing',     NULL,  false, 'sales'),
            ('31',   'مبيعات القاهرة والجيزة',      'Cairo Sales',           '3',   true,  'sales'),
            ('32',   'مبيعات الإسكندرية والدلتا',   'Alex & Delta Sales',    '3',   true,  'sales'),
            ('33',   'المناقصات والعطاءات',         'Tenders',               '3',   true,  'sales'),
            ('4',    'المخازن والنقل',              'Stores & Logistics',    NULL,  false, 'serv'),
            ('41',   'المخزن الرئيسي',              'Main Store',            '4',   true,  'serv'),
            ('42',   'النقل والتوزيع',              'Transport',             '4',   true,  'serv')
        ) AS t (code, name_ar, name_en, parent_code, is_leaf, center_type)
        ORDER BY code
    LOOP
        INSERT INTO acc.cost_centers (code, name_ar, name_en, parent_id, is_leaf, center_type)
        VALUES (r.code, r.name_ar, r.name_en,
                (SELECT id FROM acc.cost_centers WHERE code = r.parent_code), r.is_leaf, r.center_type);
    END LOOP;
END $$;
