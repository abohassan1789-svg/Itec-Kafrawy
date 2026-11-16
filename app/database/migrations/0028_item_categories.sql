-- 0028: أنواع الأصناف «الأقسام» (المرحلة 3د — شاشة «أنواع الأصناف»، النموذج 2 «شجرة» اختيار المستخدم 2026-11-16).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/12_item_categories.html — المستخدم اختار النموذج 2 وزوّد عليه: «ممكن نوع تحته أنواع،
-- وممكن نوع فرعي أفرّع منه برضه» ← شجرة بكذا مستوى؛ الأسئلة الـ 8 اتعملت بالاقتراحات):
--   * **شجرة**: `parent_id` على نفس الجدول **لحد 5 مستويات** (زي دليل الحسابات وأنواع المصروفات) — زرار
--     «+ فرعي تحت المحدد» · الأقسام الـ 6 «Panel/…» في الأكسس بقت تحت أب واحد «Panel».
--   * **الكود** `CAT-001` بيتولّد لوحده وثابت (المسلسل في `seq`).
--   * **الاسم** (الإنجليزي زي الأكسس — BMS · MCC) مطلوب ومتنضف ومايتكررش **تحت نفس الأب** حتى بحروف كبيرة/صغيرة
--     (bms = BMS) · الاسم العربي اختياري.
--   * **4 حسابات اختيارية** على النوع: المخزون · المبيعات · تكلفة المبيعات · مردودات المبيعات.
--     **الفاضي بيتورث من الأب** (أقرب أب عنده الحساب) ولو مفيش ← **الحساب الافتراضي** من «الحسابات الافتراضية»
--     (inventory · sales · cogs · sales_ret). الحسبة في `inv.item_category_account` (الفواتير هتستعملها).
--     الحساب: فرعي · نشط · من النوع الصح (المخزون = أصل بدور «مخزون» · المبيعات = إيراد تشغيلي ·
--     التكلفة = تكلفة مبيعات · المردودات = مردودات). قرار «المخزون حساب واحد + بُعد المخزن» زي ما هو.
--   * **«الأصناف الجديدة خدمية»** على النوع: الصنف الجديد تحته بيطلع خدمي (من غير مخزون) افتراضياً.
--   * «النوع» على الصنف **اختياري** و**على الصنف بس** (الفواتير في الأكسس كلها على المخزن 757 مش على قسم).
--   * الأقسام الـ 12 من الأكسس + «Panel» أب بيتعملوا هنا (أسامي بس) · «zxxxxxxx» (تجربة) ماينتقلش ·
--     `access_name` = اسم القسم في الأكسس (لنقل الأصناف — المرحلة 11).
-- لسه (شاشة الأصناف): النوع اللي عليه أصناف مايتمسحش (FK RESTRICT من الأصناف) · الموقوف مايتختارش لصنف جديد.

CREATE TABLE inv.item_categories (
    id                       integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq                      integer       NOT NULL,
    code                     varchar(20)   GENERATED ALWAYS AS
                                 ('CAT-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name                     varchar(120)  NOT NULL,                     -- زي الأكسس: BMS · MCC · Panel
    name_ar                  varchar(120),
    parent_id                integer       REFERENCES inv.item_categories (id) ON DELETE RESTRICT,
    inventory_account_id     integer       REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    sales_account_id         integer       REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    cogs_account_id          integer       REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    sales_return_account_id  integer       REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    new_items_service        boolean       NOT NULL DEFAULT false,       -- الأصناف الجديدة تحته خدمية
    access_name              varchar(60),                                -- اسم القسم في الأكسس (للنقل)
    notes                    varchar(500),
    is_active                boolean       NOT NULL DEFAULT true,
    created_at               timestamptz   NOT NULL DEFAULT now(),
    updated_at               timestamptz   NOT NULL DEFAULT now(),
    row_version              integer       NOT NULL DEFAULT 1 CONSTRAINT item_categories_row_version_chk CHECK (row_version > 0),
    CONSTRAINT item_categories_seq_unique UNIQUE (seq),
    CONSTRAINT item_categories_seq_range CHECK (seq > 0),
    -- الاسم بيتخزن متنضف (زي الماركات): من غير مسافات زيادة ولا المسافة المخفية (NBSP).
    CONSTRAINT item_categories_name_clean CHECK (
        name <> '' AND name = btrim(regexp_replace(translate(name, U&'\00A0', ' '), '\s+', ' ', 'g'))),
    CONSTRAINT item_categories_not_self CHECK (parent_id IS DISTINCT FROM id)
);
CREATE UNIQUE INDEX item_categories_code_unique ON inv.item_categories (code);
-- الاسم مايتكررش تحت نفس الأب (bms = BMS) — ونفس الاسم تحت أبين مختلفين مسموح.
CREATE UNIQUE INDEX item_categories_name_unique ON inv.item_categories (coalesce(parent_id, 0), lower(name));
CREATE INDEX item_categories_parent_idx ON inv.item_categories (parent_id);
CREATE INDEX item_categories_inventory_idx ON inv.item_categories (inventory_account_id);
CREATE INDEX item_categories_sales_idx ON inv.item_categories (sales_account_id);
CREATE INDEX item_categories_cogs_idx ON inv.item_categories (cogs_account_id);
CREATE INDEX item_categories_sales_return_idx ON inv.item_categories (sales_return_account_id);
CREATE INDEX item_categories_active_idx ON inv.item_categories (is_active);
COMMENT ON TABLE inv.item_categories IS
    'أنواع الأصناف (الأقسام): شجرة لحد 5 مستويات · كود CAT-001 · 4 حسابات اختيارية (الفاضي من الأب ثم الافتراضي)';

-- عمق النوع (1 = رئيسي) وأطول فرع تحته (0 = مفيش تحته) والنوع + كل اللي تحته.
CREATE FUNCTION inv.item_category_depth(p_id integer) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up AS (
        SELECT id, parent_id, 1 AS depth FROM inv.item_categories WHERE id = p_id
        UNION ALL
        SELECT c.id, c.parent_id, up.depth + 1 FROM inv.item_categories c JOIN up ON c.id = up.parent_id
         WHERE up.depth < 50)
    SELECT coalesce(max(depth), 0) FROM up;
$$;

CREATE FUNCTION inv.item_category_height(p_id integer) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE down AS (
        SELECT id, 0 AS height FROM inv.item_categories WHERE id = p_id
        UNION ALL
        SELECT c.id, down.height + 1 FROM inv.item_categories c JOIN down ON c.parent_id = down.id
         WHERE down.height < 50)
    SELECT coalesce(max(height), 0) FROM down;
$$;

CREATE FUNCTION inv.item_category_subtree(p_id integer) RETURNS SETOF integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE down AS (
        SELECT id FROM inv.item_categories WHERE id = p_id
        UNION
        SELECT c.id FROM inv.item_categories c JOIN down ON c.parent_id = down.id)
    SELECT id FROM down;
$$;

-- الحساب الفعلي للنوع: حسابه ← أقرب أب عنده الحساب ← الحساب الافتراضي.
-- p_kind: inventory · sales · cogs · sales_ret (نفس مفاتيح acc.default_accounts).
CREATE FUNCTION inv.item_category_account(p_id integer, p_kind text) RETURNS integer
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up AS (
        SELECT c.*, 1 AS depth FROM inv.item_categories c WHERE c.id = p_id
        UNION ALL
        SELECT c.*, up.depth + 1 FROM inv.item_categories c JOIN up ON c.id = up.parent_id WHERE up.depth < 50)
    SELECT coalesce(
        (SELECT CASE p_kind WHEN 'inventory' THEN inventory_account_id WHEN 'sales' THEN sales_account_id
                            WHEN 'cogs' THEN cogs_account_id WHEN 'sales_ret' THEN sales_return_account_id END
           FROM up
          WHERE CASE p_kind WHEN 'inventory' THEN inventory_account_id WHEN 'sales' THEN sales_account_id
                            WHEN 'cogs' THEN cogs_account_id WHEN 'sales_ret' THEN sales_return_account_id END
                IS NOT NULL
          ORDER BY depth LIMIT 1),
        (SELECT account_id FROM acc.default_accounts WHERE key = p_kind));
$$;

-- الحساب ينفع لنوع الحساب ده؟ (NULL = ينفع، غير كده = سبب الرفض)
CREATE FUNCTION inv.item_category_account_error(a acc.accounts, p_kind text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN NOT a.is_leaf THEN 'رئيسي — اختار حساب فرعي'
        WHEN p_kind = 'inventory' AND NOT (a.account_type = 'asset' AND a.role_code = 'inventory')
            THEN 'مش حساب مخزون — اختار حساب تحت «1105 المخزون»'
        WHEN p_kind = 'sales' AND NOT (a.account_type = 'revenue' AND a.classification = 'operating_rev')
            THEN 'مش حساب مبيعات — اختار حساب تحت «41 إيرادات النشاط»'
        WHEN p_kind = 'cogs' AND NOT (a.account_type = 'expense' AND a.classification = 'cogs')
            THEN 'مش حساب تكلفة مبيعات — اختار حساب تحت «51 تكلفة المبيعات»'
        WHEN p_kind = 'sales_ret' AND NOT (a.account_type = 'revenue' AND a.classification = 'returns')
            THEN 'مش حساب مردودات مبيعات — اختار حساب تحت «42 مردودات المبيعات»'
    END;
$$;

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.item_categories_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p       inv.item_categories%ROWTYPE;
    a       acc.accounts%ROWTYPE;
    kids    text;
    sub     integer;
    err     text;
    k       record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT string_agg('«' || name || '»', '، ' ORDER BY seq) INTO kids
          FROM inv.item_categories WHERE parent_id = OLD.id;
        IF kids IS NOT NULL THEN
            RAISE EXCEPTION 'النوع «% %» تحته أنواع (%) — امسحهم أو انقلهم الأول.', OLD.code, OLD.name, kids;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود النوع «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- الأب: موجود، ونشط لو النوع نشط، ومش النوع نفسه ولا حاجة تحته، ولحد 5 مستويات.
    IF NEW.parent_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id OR (NEW.is_active AND NOT OLD.is_active)) THEN
        SELECT * INTO p FROM inv.item_categories WHERE id = NEW.parent_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'النوع الأب مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NEW.is_active AND NOT p.is_active THEN
            RAISE EXCEPTION 'النوع الأب «% %» موقوف — نشّطه الأول.', p.code, p.name;
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.parent_id IS DISTINCT FROM OLD.parent_id THEN
            IF NEW.parent_id IN (SELECT inv.item_category_subtree(OLD.id)) THEN
                RAISE EXCEPTION 'مينفعش النوع «%» يبقى تحت نفسه أو تحت نوع متفرّع منه.', OLD.name;
            END IF;
        END IF;
        sub := 0;
        IF TG_OP = 'UPDATE' THEN
            sub := inv.item_category_height(OLD.id);
        END IF;
        IF inv.item_category_depth(NEW.parent_id) + 1 + sub > 5 THEN
            RAISE EXCEPTION 'أنواع الأصناف لحد 5 مستويات بس — «%» هيبقى أعمق من كده.', NEW.name;
        END IF;
    END IF;

    -- مايتوقفش وتحته أنواع نشطة.
    IF TG_OP = 'UPDATE' AND OLD.is_active AND NOT NEW.is_active THEN
        SELECT string_agg('«' || name || '»', '، ' ORDER BY seq) INTO kids
          FROM inv.item_categories WHERE parent_id = OLD.id AND is_active;
        IF kids IS NOT NULL THEN
            RAISE EXCEPTION 'النوع «% %» تحته أنواع نشطة (%) — وقّفهم الأول.', OLD.code, OLD.name, kids;
        END IF;
    END IF;

    -- الحسابات: فرعي نشط من النوع الصح (في الإضافة / التغيير / التنشيط).
    FOR k IN
        SELECT * FROM (VALUES
            ('inventory', 'المخزون',          NEW.inventory_account_id,
             CASE WHEN TG_OP = 'UPDATE' THEN OLD.inventory_account_id END),
            ('sales',     'المبيعات',         NEW.sales_account_id,
             CASE WHEN TG_OP = 'UPDATE' THEN OLD.sales_account_id END),
            ('cogs',      'تكلفة المبيعات',   NEW.cogs_account_id,
             CASE WHEN TG_OP = 'UPDATE' THEN OLD.cogs_account_id END),
            ('sales_ret', 'مردودات المبيعات', NEW.sales_return_account_id,
             CASE WHEN TG_OP = 'UPDATE' THEN OLD.sales_return_account_id END)
        ) AS t (kind, label, new_id, old_id)
    LOOP
        CONTINUE WHEN k.new_id IS NULL;
        CONTINUE WHEN TG_OP = 'UPDATE' AND k.new_id = k.old_id AND NOT (NEW.is_active AND NOT OLD.is_active);
        SELECT * INTO a FROM acc.accounts WHERE id = k.new_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'حساب % للنوع «%» مش موجود (ممكن يكون اتمسح).', k.label, NEW.name;
        END IF;
        IF TG_OP = 'INSERT' OR k.new_id IS DISTINCT FROM k.old_id THEN
            err := inv.item_category_account_error(a, k.kind);
            IF err IS NOT NULL THEN
                RAISE EXCEPTION 'حساب % «% %» %.', k.label, a.code, a.name_ar, err;
            END IF;
        END IF;
        IF NEW.is_active AND NOT a.is_active THEN
            RAISE EXCEPTION 'حساب % «% %» موقوف — نشّطه من شجرة الحسابات الأول.', k.label, a.code, a.name_ar;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER item_categories_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON inv.item_categories
    FOR EACH ROW EXECUTE FUNCTION inv.item_categories_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.item_categories
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- حماية حساب النوع من شجرة الحسابات: مايتمسحش، ومايتحوّلش رئيسي إلا بالتفريع (النوع بينزل على «— عام»)،
-- ومايتوقفش وعليه نوع نشط، ومايتغيّرش نوعه ولا تصنيفه ولا دوره.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_item_categories_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked  text;
    active  text;
BEGIN
    SELECT string_agg('«' || name || '»', '، ' ORDER BY seq),
           string_agg('«' || name || '»', '، ' ORDER BY seq) FILTER (WHERE is_active)
      INTO linked, active
      FROM inv.item_categories
     WHERE OLD.id IN (inventory_account_id, sales_account_id, cogs_account_id, sales_return_account_id);
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع الأصناف % — غيّر حساب النوع الأول.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf
       AND coalesce(current_setting('acc.split_account_id', true), '') <> OLD.id::text THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع الأصناف % — فرّعه بزرار «+ فرعي تحت المحدد» (الأنواع بتنزل على «— عام»).',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF NEW.account_type <> OLD.account_type OR NEW.classification <> OLD.classification
       OR NEW.role_code IS DISTINCT FROM OLD.role_code THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بنوع الأصناف % — مينفعش نوعه أو تصنيفه أو دوره يتغيّر.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active AND active IS NOT NULL THEN
        RAISE EXCEPTION 'الحساب «% %» عليه أنواع أصناف نشطة (%) — غيّر حسابهم أو وقّفهم الأول.',
            OLD.code, OLD.name_ar, active;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zitem_categories_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_item_categories_guard();

-- ---------------------------------------------------------------------------
-- التفريع: نفس acc.split_account بتاعة 0025 بالظبط + حسابات أنواع الأصناف بتنزل على «— عام».
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

    -- 8) حسابات أنواع الأصناف (0028) بتنزل على العام.
    UPDATE inv.item_categories SET inventory_account_id = v_general WHERE inventory_account_id = p.id;
    UPDATE inv.item_categories SET sales_account_id = v_general WHERE sales_account_id = p.id;
    UPDATE inv.item_categories SET cogs_account_id = v_general WHERE cogs_account_id = p.id;
    UPDATE inv.item_categories SET sales_return_account_id = v_general WHERE sales_return_account_id = p.id;
    RETURN v_general;
END $$;

-- ---------------------------------------------------------------------------
-- الأقسام من الأكسس (tb-Shopes) — «Panel» أب للـ 6 «Panel/…» · «zxxxxxxx» ماينتقلش.
-- «Installation» مبيعاتها على «4102 إيرادات تركيبات وخدمات» (لو لسه فرعي نشط) — والباقي على الافتراضي.
-- ---------------------------------------------------------------------------
INSERT INTO inv.item_categories (seq, name, name_ar, access_name, sales_account_id)
VALUES (1, 'BMS', 'أنظمة إدارة المباني', 'BMS', NULL),
       (2, 'Installation', 'تركيبات', 'Installation',
        (SELECT id FROM acc.accounts WHERE code = '4102' AND is_leaf AND is_active AND account_type = 'revenue'
            AND classification = 'operating_rev')),
       (3, 'MCC', 'لوحات تحكم المحركات', 'MCC', NULL),
       (4, 'Office', 'مكتب', 'Office', NULL),
       (5, 'Panel', 'لوحات', NULL, NULL);

INSERT INTO inv.item_categories (seq, name, parent_id, access_name)
SELECT t.seq, t.name, (SELECT id FROM inv.item_categories WHERE seq = 5), 'Panel/' || t.name
  FROM (VALUES (6, 'ABB'), (7, 'HIMEL'), (8, 'ide'), (9, 'KSA'), (10, 'Nara'), (11, 'Rittal')) AS t (seq, name)
 ORDER BY t.seq;

INSERT INTO inv.item_categories (seq, name, name_ar, access_name)
VALUES (12, 'Workshop', 'الورشة', 'Workshop'),
       (13, 'FIRE ALARM', 'إنذار حريق', 'FIRE ALARM');
