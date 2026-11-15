-- 0025: أنواع المصروفات (المرحلة 3ج — شاشة «أنواع المصروفات»، النموذج 4 اختيار المستخدم 2026-11-15).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/09_expense_types.html — ردود المستخدم 2026-11-15):
--   * الشاشة لتعريف النوع بس — المصروف نفسه (والقيد: مدين حساب النوع / دائن الخزنة أو البنك) في المرحلة 9.
--   * **الكود** `EXT-001`, `EXT-002`… بيتولّد لوحده وثابت (زي TRS / BNK): المسلسل في `seq`.
--   * **كل نوع مربوط بحساب مصروف فرعي** في الدليل: **يتختار حساب موجود** أو **يتعمل حساب جديد** باسم النوع تحت
--     حساب رئيسي من المصروفات (52 / 53 / 54…). **كذا نوع ممكن يبقوا على نفس الحساب** (كهرباء ومياه ← 5203).
--     الحساب: فرعي · نشط · نوعه «مصروفات» · من غير دور نظامي (الأدوار بتتقيّد لوحدها من المستندات) · مش تكلفة مبيعات.
--     الحساب ثابت بعد الحفظ (إلا لما الحساب يتفرّع ← الأنواع بتنزل على «— عام»).
--   * **النوع بيتفرّع** (رد المستخدم: «النوع الواحد يتفرع منه أكثر من نوع»): `parent_id` على نفس الجدول لحد 5 مستويات،
--     والنوع الفرعي ليه حسابه (افتراضي = حساب الأب، ويتغيّر).
--   * **أمر التوريد اختياري لكل الأنواع** (مفيش خانة) · **مفيش مركز تكلفة افتراضي ولا خانة ض.ق.م** على النوع.
--   * الأنواع العشرة من الأكسس (typeExpens) بتتعمل هنا بعد تصحيح الإملا، ومعاها 5 حسابات جديدة:
--     5207 تليفونات وإنترنت · 5208 انتقالات ومواصلات · 5209 إكراميات وضيافة (تحت 52) ·
--     5304 وقود وسولار · 5305 صيانة سيارات (تحت 53).
--   * الشاشة تحت «الحسابات» (مش قسم جديد) — في `permission_registry`.
-- لسه (المرحلة 9): النوع اللي عليه مصروف مايتمسحش (FK RESTRICT من المصروفات) · الموقوف مايتختارش في مصروف جديد ·
--   هل المصروف يتسجل على النوع الرئيسي ولا الفرعي بس.

-- ---------------------------------------------------------------------------
-- الحسابات الخمسة الجديدة (لو الأب رئيسي نشط والكود فاضي — لو مااتعملتش النوع بياخد «مصروفات نثرية»).
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
         ('5207', 'تليفونات وإنترنت',   'Telephone & Internet',      '52'),
         ('5208', 'انتقالات ومواصلات',  'Transportation',            '52'),
         ('5209', 'إكراميات وضيافة',    'Tips & Hospitality',        '52'),
         ('5304', 'وقود وسولار',        'Fuel & Diesel',             '53'),
         ('5305', 'صيانة سيارات',       'Vehicle Maintenance',       '53')
        ) AS t (code, name_ar, name_en, parent_code)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM acc.accounts WHERE code = r.code)
           AND EXISTS (SELECT 1 FROM acc.accounts WHERE code = r.parent_code AND NOT is_leaf AND is_active
                                                     AND account_type = 'expense') THEN
            INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                                      cash_flow, is_system, is_active)
            VALUES (r.code, r.name_ar, r.name_en, (SELECT id FROM acc.accounts WHERE code = r.parent_code), true,
                    'expense', 'dr', 'operating_exp', 'op', false, true);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- الجدول
-- ---------------------------------------------------------------------------
CREATE TABLE exp.expense_types (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq             integer       NOT NULL,
    code            varchar(20)   GENERATED ALWAYS AS
                        ('EXT-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar         varchar(120)  NOT NULL,                             -- 120 = طول اسم الحساب
    name_en         varchar(120),
    parent_id       integer       REFERENCES exp.expense_types (id) ON DELETE RESTRICT,
    account_id      integer       NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    owns_account    boolean       NOT NULL DEFAULT false,               -- الحساب اتعمل مع النوع (بيتمسح معاه)
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT expense_types_row_version_chk CHECK (row_version > 0),
    CONSTRAINT expense_types_seq_unique UNIQUE (seq),
    CONSTRAINT expense_types_seq_range CHECK (seq > 0),
    CONSTRAINT expense_types_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT expense_types_not_self CHECK (parent_id IS DISTINCT FROM id)
);
CREATE UNIQUE INDEX expense_types_code_unique ON exp.expense_types (code);
CREATE UNIQUE INDEX expense_types_name_unique ON exp.expense_types (lower(btrim(name_ar)));
CREATE INDEX expense_types_parent_idx ON exp.expense_types (parent_id);
CREATE INDEX expense_types_account_idx ON exp.expense_types (account_id);
CREATE INDEX expense_types_active_idx ON exp.expense_types (is_active);
COMMENT ON TABLE exp.expense_types IS
    'أنواع المصروفات: كل نوع مربوط بحساب مصروف فرعي (كذا نوع على نفس الحساب مسموح) · بيتفرّع لحد 5 مستويات · '
    'الكود EXT-001';

-- عمق النوع (1 = رئيسي) وأطول فرع تحته (0 = مفيش تحته).
CREATE FUNCTION exp.expense_type_depth(p_id integer) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up AS (
        SELECT id, parent_id, 1 AS depth FROM exp.expense_types WHERE id = p_id
        UNION ALL
        SELECT t.id, t.parent_id, up.depth + 1 FROM exp.expense_types t JOIN up ON t.id = up.parent_id
         WHERE up.depth < 50)
    SELECT coalesce(max(depth), 0) FROM up;
$$;

CREATE FUNCTION exp.expense_type_height(p_id integer) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE down AS (
        SELECT id, 0 AS height FROM exp.expense_types WHERE id = p_id
        UNION ALL
        SELECT t.id, down.height + 1 FROM exp.expense_types t JOIN down ON t.parent_id = down.id
         WHERE down.height < 50)
    SELECT coalesce(max(height), 0) FROM down;
$$;

-- النوع + كل اللي تحته (فلتر «الأب بيجيب الأبناء» في تقارير المصروفات).
CREATE FUNCTION exp.expense_type_subtree(p_id integer) RETURNS SETOF integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE down AS (
        SELECT id FROM exp.expense_types WHERE id = p_id
        UNION
        SELECT t.id FROM exp.expense_types t JOIN down ON t.parent_id = down.id)
    SELECT id FROM down;
$$;

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION exp.expense_types_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a       acc.accounts%ROWTYPE;
    p       exp.expense_types%ROWTYPE;
    kids    text;
    sub     integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT string_agg('«' || name_ar || '»', '، ' ORDER BY seq) INTO kids
          FROM exp.expense_types WHERE parent_id = OLD.id;
        IF kids IS NOT NULL THEN
            RAISE EXCEPTION 'النوع «% %» تحته أنواع (%) — امسحهم أو انقلهم الأول.', OLD.code, OLD.name_ar, kids;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود النوع «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        IF (NEW.account_id <> OLD.account_id OR NEW.owns_account <> OLD.owns_account)
           AND coalesce(current_setting('exp.type_account_move', true), '') <> '1' THEN
            RAISE EXCEPTION 'حساب النوع «%» ثابت بعد الحفظ — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- النوع الأب: موجود، ونشط لو النوع نشط، ومش النوع نفسه ولا حاجة تحته، ولحد 5 مستويات.
    IF NEW.parent_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id OR (NEW.is_active AND NOT OLD.is_active)) THEN
        SELECT * INTO p FROM exp.expense_types WHERE id = NEW.parent_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'النوع الأب مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NEW.is_active AND NOT p.is_active THEN
            RAISE EXCEPTION 'النوع الأب «% %» موقوف — نشّطه الأول.', p.code, p.name_ar;
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
            IF NEW.parent_id IN (SELECT exp.expense_type_subtree(OLD.id)) THEN
                RAISE EXCEPTION 'مينفعش النوع «%» يبقى تحت نفسه أو تحت نوع متفرّع منه.', OLD.name_ar;
            END IF;
        END IF;
        sub := 0;                                       -- (CASE جوه IF بيلخبط الـ THEN في plpgsql)
        IF TG_OP = 'UPDATE' THEN
            sub := exp.expense_type_height(OLD.id);
        END IF;
        IF exp.expense_type_depth(NEW.parent_id) + 1 + sub > 5 THEN
            RAISE EXCEPTION 'أنواع المصروفات لحد 5 مستويات بس — «%» هيبقى أعمق من كده.', NEW.name_ar;
        END IF;
    END IF;

    -- مايتوقفش وتحته أنواع نشطة.
    IF TG_OP = 'UPDATE' AND OLD.is_active AND NOT NEW.is_active THEN
        SELECT string_agg('«' || name_ar || '»', '، ' ORDER BY seq) INTO kids
          FROM exp.expense_types WHERE parent_id = OLD.id AND is_active;
        IF kids IS NOT NULL THEN
            RAISE EXCEPTION 'النوع «% %» تحته أنواع نشطة (%) — وقّفهم الأول.', OLD.code, OLD.name_ar, kids;
        END IF;
    END IF;

    -- الحساب: فرعي نشط من المصروفات ومن غير دور نظامي (في الإضافة / النقل للعام / التنشيط).
    IF TG_OP = 'INSERT' OR NEW.account_id <> OLD.account_id OR (NEW.is_active AND NOT OLD.is_active) THEN
        SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'حساب النوع مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF TG_OP = 'INSERT' OR NEW.account_id <> OLD.account_id THEN
            IF NOT a.is_leaf THEN
                RAISE EXCEPTION 'الحساب «% %» رئيسي — نوع المصروف لازم يبقى حسابه فرعي.', a.code, a.name_ar;
            END IF;
            IF a.account_type <> 'expense' THEN
                RAISE EXCEPTION 'الحساب «% %» مش من المصروفات — اختار حساب تحت «5 المصروفات».', a.code, a.name_ar;
            END IF;
            IF a.classification = 'cogs' THEN
                RAISE EXCEPTION 'الحساب «% %» تكلفة مبيعات (بتتقيّد من الفواتير لوحدها) — اختار حساب مصروف تاني.',
                    a.code, a.name_ar;
            END IF;
            IF a.role_code IS NOT NULL THEN
                RAISE EXCEPTION 'الحساب «% %» عليه دور نظامي (بيتقيّد لوحده من المستندات) — اختار حساب مصروف تاني.',
                    a.code, a.name_ar;
            END IF;
        END IF;
        IF NEW.is_active AND NOT a.is_active THEN
            RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه من شجرة الحسابات الأول.', a.code, a.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER expense_types_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON exp.expense_types
    FOR EACH ROW EXECUTE FUNCTION exp.expense_types_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON exp.expense_types
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- بعد التعديل: اسم الحساب اللي اتعمل مع النوع بيتغيّر معاه (لو كان زي اسم النوع القديم).
CREATE FUNCTION exp.expense_types_sync_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.owns_account AND NEW.name_ar IS DISTINCT FROM OLD.name_ar THEN
        UPDATE acc.accounts SET name_ar = NEW.name_ar
         WHERE id = NEW.account_id AND name_ar = OLD.name_ar
           AND NOT EXISTS (SELECT 1 FROM exp.expense_types t WHERE t.account_id = NEW.account_id AND t.id <> NEW.id);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER expense_types_sync_account_trg
    AFTER UPDATE ON exp.expense_types
    FOR EACH ROW EXECUTE FUNCTION exp.expense_types_sync_account();

-- ---------------------------------------------------------------------------
-- حماية حساب النوع من شجرة الحسابات: مايتمسحش، ومايتحوّلش رئيسي إلا بالتفريع (الأنواع بتنزل على «— عام»)،
-- ومايتوقفش وعليه نوع نشط، ومايبقاش عليه دور ولا يخرج من المصروفات.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_expense_types_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked  text;
    active  text;
BEGIN
    SELECT string_agg('«' || name_ar || '»', '، ' ORDER BY seq),
           string_agg('«' || name_ar || '»', '، ' ORDER BY seq) FILTER (WHERE is_active)
      INTO linked, active
      FROM exp.expense_types WHERE account_id = OLD.id;
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع المصروف % — امسح النوع أو غيّر حسابه الأول.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf
       AND coalesce(current_setting('acc.split_account_id', true), '') <> OLD.id::text THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع المصروف % — فرّعه بزرار «+ فرعي تحت المحدد» (الأنواع بتنزل على «— عام»).',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF NEW.role_code IS NOT NULL AND NEW.role_code IS DISTINCT FROM OLD.role_code THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع المصروف % — مينفعش ياخد دور نظامي.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF NEW.account_type <> OLD.account_type OR (NEW.classification = 'cogs' AND OLD.classification <> 'cogs') THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع المصروف % — لازم يفضل حساب مصروف.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active AND active IS NOT NULL THEN
        RAISE EXCEPTION 'الحساب «% %» عليه أنواع مصروفات نشطة (%) — وقّفهم الأول.', OLD.code, OLD.name_ar, active;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zexpense_types_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_expense_types_guard();

-- ---------------------------------------------------------------------------
-- التفريع: نفس acc.split_account بتاعة 0017 بالظبط + أنواع المصروفات بتنزل على «— عام».
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acc.split_account(p_account_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم الحساب «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    p              acc.accounts%ROWTYPE;
    v_sales        integer[];
    v_purchase     integer[];
    v_general      integer;
    v_code         varchar(20);
    v_suffix       constant text := ' — عام';
BEGIN
    -- 1) القفل: أي جهاز تاني بيعدّل / بيفرّع / بيربط ضريبة على الحساب ده بيستنى لحد ما نخلص.
    SELECT * INTO p FROM acc.accounts WHERE id = p_account_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الحساب ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT p.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» رئيسي أصلاً — ضيف تحته على طول.', p.code, p.name_ar;
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'الحساب «% %» في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.', p.code, p.name_ar;
    END IF;
    IF NOT p.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه الأول وبعدين فرّع منه.', p.code, p.name_ar;
    END IF;

    SELECT array_agg(id) FILTER (WHERE sales_account_id = p.id),
           array_agg(id) FILTER (WHERE purchase_account_id = p.id)
      INTO v_sales, v_purchase
      FROM acc.tax_codes WHERE p.id IN (sales_account_id, purchase_account_id);

    -- 2) فك ربط الضريبة مؤقتاً (بيرجع على «العام» تحت) — من غير استثناء في قاعدة الضريبة.
    UPDATE acc.tax_codes SET sales_account_id = NULL WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = NULL WHERE id = ANY (v_purchase);

    -- 3) الحساب يتحوّل رئيسي (الاستثناء للحساب ده بس، وبيتقفل تاني على طول).
    PERFORM set_config('acc.split_account_id', p.id::text, true);
    UPDATE acc.accounts SET is_leaf = false WHERE id = p.id;
    PERFORM set_config('acc.split_account_id', '', true);

    -- 4) أول حساب تحته «— عام» (مع كل تفريع) بياخد الدور والنظامي والربط وكل اللي بيتورث.
    v_code := coalesce(nullif(btrim(p_general_code), ''),
                       p.code || CASE WHEN p.level <= 1 THEN '1' ELSE '01' END);
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, party_kind, requires_cost_center, currency_code,
                              is_active, is_system)
    VALUES (v_code, left(p.name_ar, 120 - length(v_suffix)) || v_suffix,
            CASE WHEN p.name_en IS NULL THEN NULL ELSE left(p.name_en, 110) || ' — General' END,
            p.id, true, p.account_type, p.nature, p.classification, p.cash_flow, p.role_code, p.party_kind,
            p.requires_cost_center, p.currency_code, true, p.is_system)
    RETURNING id INTO v_general;

    -- 5) TODO المرحلة 4 (القيود) — قرار المستخدم (أ): نقل الحركة القديمة للعام في نفس الـ transaction:
    --    UPDATE acc.journal_lines SET account_id = v_general WHERE account_id = p.id;
    --    (سؤال مفتوح: الأرصدة المجمّعة لو اتعملت جدول لوحدها بتتنقل هنا كمان.)

    -- 6) ربط أكواد الضريبة والحسابات الافتراضية ومجموعات العملاء والموردين يرجعوا على العام.
    UPDATE acc.tax_codes SET sales_account_id = v_general WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = v_general WHERE id = ANY (v_purchase);
    UPDATE acc.default_accounts SET account_id = v_general WHERE account_id = p.id;
    UPDATE party.customer_groups SET control_account_id = v_general WHERE control_account_id = p.id;
    UPDATE party.supplier_groups SET control_account_id = v_general WHERE control_account_id = p.id;

    -- 7) أنواع المصروفات (0025) بتنزل على العام (والعام مابقاش «اتعمل مع النوع»).
    PERFORM set_config('exp.type_account_move', '1', true);
    UPDATE exp.expense_types SET account_id = v_general, owns_account = false WHERE account_id = p.id;
    PERFORM set_config('exp.type_account_move', '', true);
    RETURN v_general;
END $$;

-- ---------------------------------------------------------------------------
-- الأنواع العشرة من الأكسس (بعد تصحيح الإملا) — الحساب لو مش مناسب ← «5204 مصروفات نثرية».
-- ---------------------------------------------------------------------------
INSERT INTO exp.expense_types (seq, name_ar, name_en, account_id)
SELECT t.seq, t.name_ar, t.name_en,
       coalesce(
           (SELECT a.id FROM acc.accounts a WHERE a.code = t.code AND a.is_leaf AND a.is_active
               AND a.account_type = 'expense' AND a.role_code IS NULL AND a.classification <> 'cogs'),
           (SELECT a.id FROM acc.accounts a WHERE a.code = '5204' AND a.is_leaf AND a.is_active
               AND a.account_type = 'expense' AND a.role_code IS NULL))
  FROM (VALUES (1,  'كهرباء',            'Electricity',      '5203'),
               (2,  'إيجارات',           'Rent',             '5202'),
               (3,  'مصاريف عامة',       'General Expenses', '5204'),
               (4,  'مصاريف نقل بضاعة',  'Goods Freight',    '5301'),
               (5,  'مصاريف تليفونات',   'Telephone',        '5207'),
               (6,  'إكراميات',          'Tips',             '5209'),
               (7,  'مياه',              'Water',            '5203'),
               (8,  'سولار',             'Diesel',           '5304'),
               (9,  'صيانة سيارات',      'Car Maintenance',  '5305'),
               (10, 'مواصلات',           'Transportation',   '5208')) AS t (seq, name_ar, name_en, code)
 WHERE EXISTS (SELECT 1 FROM acc.accounts a WHERE a.code IN (t.code, '5204') AND a.is_leaf AND a.is_active
                  AND a.account_type = 'expense' AND a.role_code IS NULL)
 ORDER BY t.seq;
