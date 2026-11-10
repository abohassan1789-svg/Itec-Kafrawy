-- 0012: الحسابات الافتراضية (المرحلة 2 — شاشة «الحسابات الافتراضية»، النموذج 9 اختيار المستخدم 2026-11-09).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- جدول **مفتاح ← حساب** (acc.default_accounts): كل مستند بياخد حساباته من هنا — فاتورة البيع ← «المبيعات»
-- و«العملاء» و«ض.ق.م مخرجات» و«تكلفة المبيعات» و«المخزون» — **من غير أي بحث بالاسم**.
-- ليه مش «الدور» لوحده؟ لأن أكتر من حساب ممكن يبقى ليه نفس الدور (بنكين، أو «… — عام» بعد التفريع)،
-- فلازم نقول **مين بالظبط** الافتراضي. الدور بيفضل «اقتراح» (زرار «اقترح من الدور»).
--
--   * المفاتيح **ثابتة** (29 مفتاح في 7 أقسام): مفيش إضافة ولا مسح من البرنامج — المستخدم بيغيّر الحساب بس.
--     أي موديول جديد محتاج مفتاح بيضيفه في السكريبت بتاعه.
--   * كل مفتاح ليه **نوع مسموح** (المبيعات = إيرادات، المخزون = أصول…) والحساب لازم **فرعي نشط** من النوع ده.
--   * الحساب الافتراضي **مايتمسحش ولا يتوقف ولا يتحوّل رئيسي** — إلا بالتفريع (acc.split_account) وساعتها
--     المفتاح بيروح لـ «… — عام» لوحده في نفس الـ transaction.
--   * row_version لكل مفتاح (جهازين على نفس الشاشة).
--   * 5 حسابات كانت ناقصة في الدليل بتتضاف هنا بأدوار جديدة (خصم مسموح به، خصم مكتسب، تسوية الجرد،
--     فروق التكلفة، فروق التقريب) — فالشاشة بتفتح وكل المفاتيح متحددة.

-- ---------------------------------------------------------------------------
-- 1) أدوار وحسابات جديدة في الدليل
-- ---------------------------------------------------------------------------
INSERT INTO acc.account_roles (code, name_ar, name_en, account_type, default_party_kind, sort_order) VALUES
    ('disc_allowed',  'خصم مسموح به',  'Discount Allowed',      'expense', NULL, 24),
    ('disc_received', 'خصم مكتسب',     'Discount Received',     'revenue', NULL, 25),
    ('inv_adj',       'تسوية الجرد',   'Inventory Adjustment',  'expense', NULL, 26),
    ('cost_var',      'فروق التكلفة',  'Cost Variance',         'expense', NULL, 27),
    ('rounding',      'فروق التقريب',  'Rounding Differences',  'expense', NULL, 28);

-- بتتضاف بس لو الأب موجود رئيسي ونشط والكود فاضي (لو مااتضافتش المفتاح بيفضل ناقص ويتحدد من الشاشة).
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
         ('5303', 'خصم مسموح به',                     'Discount Allowed',           '53', 'dr', 'operating_exp', 'disc_allowed'),
         ('4302', 'خصم مكتسب',                        'Discount Received',          '43', 'cr', 'other_rev',     'disc_received'),
         ('5205', 'تسوية جرد المخزون (عجز / زيادة)',  'Inventory Count Adjustment', '52', 'dr', 'operating_exp', 'inv_adj'),
         ('5206', 'فروق تكلفة المخزون',               'Inventory Cost Variance',    '52', 'dr', 'operating_exp', 'cost_var'),
         ('5403', 'فروق التقريب',                     'Rounding Differences',       '54', 'dr', 'finance_exp',   'rounding')
        ) AS t (code, name_ar, name_en, parent_code, nature, classification, role_code)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM acc.accounts WHERE code = r.code)
           AND EXISTS (SELECT 1 FROM acc.accounts WHERE code = r.parent_code AND NOT is_leaf AND is_active) THEN
            INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                                      cash_flow, role_code, is_system, is_active)
            VALUES (r.code, r.name_ar, r.name_en, (SELECT id FROM acc.accounts WHERE code = r.parent_code), true,
                    CASE left(r.code, 1) WHEN '4' THEN 'revenue' ELSE 'expense' END,
                    r.nature, r.classification, 'op', r.role_code, true, true);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 2) الجدول
-- ---------------------------------------------------------------------------
CREATE TABLE acc.default_accounts (
    key           varchar(30)  PRIMARY KEY,
    group_code    varchar(10)  NOT NULL,
    name_ar       varchar(80)  NOT NULL,
    account_type  varchar(10)  NOT NULL,
    role_code     varchar(20)  REFERENCES acc.account_roles (code) ON DELETE RESTRICT,   -- الاقتراح
    used_in       varchar(120),
    sort_order    smallint     NOT NULL,
    account_id    integer      REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    row_version   integer      NOT NULL DEFAULT 1 CONSTRAINT default_accounts_row_version_chk CHECK (row_version > 0),
    CONSTRAINT default_accounts_key_chk CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT default_accounts_group_chk CHECK (group_code IN ('sales', 'purch', 'inv', 'cash', 'tax', 'fx', 'close')),
    CONSTRAINT default_accounts_type_chk CHECK (account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense'))
);
CREATE INDEX default_accounts_account_idx ON acc.default_accounts (account_id);
CREATE INDEX default_accounts_group_idx ON acc.default_accounts (group_code, sort_order);
CREATE INDEX default_accounts_role_idx ON acc.default_accounts (role_code);
COMMENT ON TABLE acc.default_accounts IS
    'الحسابات الافتراضية: مفتاح ثابت ← حساب فرعي نشط من النوع المسموح (المستندات بتاخد حساباتها من هنا)';

-- الحساب لازم فرعي نشط من النوع المسموح · المفتاح ونوعه ثابتين.
CREATE FUNCTION acc.default_accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a acc.accounts%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.key <> OLD.key OR NEW.account_type <> OLD.account_type THEN
            RAISE EXCEPTION 'المفتاح «%» ونوعه ثابتين — غيّر الحساب بس.', OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;
    IF NEW.account_id IS NOT NULL AND (TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
        -- FOR SHARE: لو جهاز تاني بيوقف الحساب أو بيحوّله رئيسي في نفس اللحظة، واحد يستنى التاني.
        SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الحساب مش موجود في الدليل (ممكن يكون اتمسح).';
        END IF;
        IF NOT a.is_leaf OR NOT a.is_active THEN
            RAISE EXCEPTION 'الحساب «% %» لازم يبقى فرعي ونشط (بيقبل قيود) عشان يبقى افتراضي لـ «%».',
                a.code, a.name_ar, NEW.name_ar;
        END IF;
        IF a.account_type <> NEW.account_type THEN
            RAISE EXCEPTION '«%» محتاج حساب من «%» — والحساب «% %» من «%».', NEW.name_ar,
                CASE NEW.account_type WHEN 'asset' THEN 'الأصول' WHEN 'liability' THEN 'الخصوم'
                     WHEN 'equity' THEN 'حقوق الملكية' WHEN 'revenue' THEN 'الإيرادات' ELSE 'المصروفات' END,
                a.code, a.name_ar,
                CASE a.account_type WHEN 'asset' THEN 'الأصول' WHEN 'liability' THEN 'الخصوم'
                     WHEN 'equity' THEN 'حقوق الملكية' WHEN 'revenue' THEN 'الإيرادات' ELSE 'المصروفات' END;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER default_accounts_guard_trg
    BEFORE INSERT OR UPDATE ON acc.default_accounts
    FOR EACH ROW EXECUTE FUNCTION acc.default_accounts_guard();

CREATE FUNCTION acc.default_accounts_delete_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'المفتاح «%» ثابت — مايتمسحش (غيّر الحساب بس).', OLD.name_ar;
END $$;
CREATE TRIGGER default_accounts_delete_guard_trg
    BEFORE DELETE ON acc.default_accounts
    FOR EACH ROW EXECUTE FUNCTION acc.default_accounts_delete_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.default_accounts
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- 3) الحساب الافتراضي: مايتمسحش ولا يتوقف ولا يتحوّل رئيسي (رسالة عربي بدل خطأ الـ FK).
--    (اسم الـ trigger بيبدأ بـ accounts_z عشان رسايل «نظامي» و«ضريبة» تطلع الأول — بيترتبوا بالاسم.)
--    الاستثناء: التفريع (acc.split_account) للحساب ده بس — والمفتاح بيروح للعام في نفس الـ transaction.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_default_links_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg(name_ar, '، ' ORDER BY sort_order) INTO linked
    FROM acc.default_accounts WHERE account_id = OLD.id;
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» افتراضي لـ (%) — غيّر الحساب الافتراضي الأول.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf
       AND coalesce(current_setting('acc.split_account_id', true), '') = OLD.id::text THEN
        RETURN NEW;                                   -- التفريع: المفتاح بيتنقل للعام جوه الدالة
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR (OLD.is_leaf AND NOT NEW.is_leaf) THEN
        RAISE EXCEPTION 'الحساب «% %» افتراضي لـ (%) — مينفعش يتوقف ولا يتحوّل رئيسي.', OLD.code, OLD.name_ar, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zdefault_links_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_default_links_guard();

-- ---------------------------------------------------------------------------
-- 4) التفريع: نفس 0011 بالظبط + نقل الحسابات الافتراضية لـ «— عام»
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

    -- 6) ربط أكواد الضريبة والحسابات الافتراضية يرجعوا على العام.
    UPDATE acc.tax_codes SET sales_account_id = v_general WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = v_general WHERE id = ANY (v_purchase);
    UPDATE acc.default_accounts SET account_id = v_general WHERE account_id = p.id;
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_account(integer, varchar) IS
    'تفريع حساب فرعي: يتحوّل رئيسي + «<الاسم> — عام» بالدور والنظامي وربط الضريبة والحسابات الافتراضية (والقيود — المرحلة 4)';

-- ---------------------------------------------------------------------------
-- 5) المفاتيح (29) — الحساب أول مرة = أول حساب فرعي نشط بالدور (من غير بحث بالاسم).
-- ---------------------------------------------------------------------------
INSERT INTO acc.default_accounts (key, group_code, name_ar, account_type, role_code, used_in, sort_order) VALUES
    ('sales',         'sales', 'المبيعات (بضاعة)',          'revenue',   'sales',         'فاتورة بيع',                   1),
    ('services',      'sales', 'إيراد الخدمات والتركيبات',  'revenue',   NULL,            'فاتورة خدمة',                  2),
    ('sales_ret',     'sales', 'مردودات المبيعات',          'revenue',   'sales_ret',     'مردود بيع',                    3),
    ('disc_allowed',  'sales', 'خصم مسموح به',              'expense',   'disc_allowed',  'فاتورة بيع · سند قبض',         4),
    ('customers',     'sales', 'العملاء (حساب الرقابة)',    'asset',     'customers',     'فاتورة بيع · سند قبض',         5),
    ('cust_adv',      'sales', 'دفعات مقدمة من العملاء',    'liability', 'advances',      'سند قبض مقدّم',                6),
    ('suppliers',     'purch', 'الموردين (حساب الرقابة)',   'liability', 'suppliers',     'فاتورة شراء · سند صرف',        7),
    ('disc_received', 'purch', 'خصم مكتسب',                 'revenue',   'disc_received', 'فاتورة شراء · سند صرف',        8),
    ('inventory',     'inv',   'المخزون',                   'asset',     'inventory',     'كل حركات المخزن',              9),
    ('cogs',          'inv',   'تكلفة المبيعات',            'expense',   'cogs',          'فاتورة بيع · مردود بيع',      10),
    ('inv_adj',       'inv',   'تسوية الجرد (عجز / زيادة)', 'expense',   'inv_adj',       'تسوية جرد',                   11),
    ('cost_var',      'inv',   'فروق التكلفة',              'expense',   'cost_var',      'تحويل · تسوية',               12),
    ('cash_box',      'cash',  'الخزينة الافتراضية',        'asset',     'cash',          'سند قبض · سند صرف',           13),
    ('bank',          'cash',  'البنك الافتراضي',           'asset',     'bank',          'إيداع · تحويل بنكي',          14),
    ('notes_rec',     'cash',  'أوراق القبض (شيكات)',       'asset',     'notes_rec',     'شيك وارد',                    15),
    ('notes_pay',     'cash',  'أوراق الدفع (شيكات)',       'liability', 'notes_pay',     'شيك صادر',                    16),
    ('bank_charges',  'cash',  'مصاريف بنكية',              'expense',   NULL,            'كشف البنك',                   17),
    ('custody',       'cash',  'عهد الموظفين',              'asset',     'custody',       'صرف / تسوية عهدة',            18),
    ('vat_out',       'tax',   'ض.ق.م مخرجات',              'liability', 'vat_out',       'فاتورة بيع (لو كود الضريبة فاضي)',  19),
    ('vat_in',        'tax',   'ض.ق.م مدخلات',              'asset',     'vat_in',        'فاتورة شراء (لو كود الضريبة فاضي)', 20),
    ('wht_rec',       'tax',   'خصم من المنبع (لينا)',      'asset',     'wht_rec',       'سند قبض',                     21),
    ('wht_pay',       'tax',   'خصم من المنبع (علينا)',     'liability', 'wht_pay',       'سند صرف',                     22),
    ('fx_gain',       'fx',    'فروق عملة دائنة',           'revenue',   'fx_gain',       'سداد بعملة · إعادة تقييم',    23),
    ('fx_loss',       'fx',    'فروق عملة مدينة',           'expense',   'fx_loss',       'سداد بعملة · إعادة تقييم',    24),
    ('rounding',      'fx',    'فروق التقريب',              'expense',   'rounding',      'أي قيد فيه كسور',             25),
    ('opening',       'close', 'الأرصدة الافتتاحية',        'equity',    'opening',       'نقل الأرصدة من الأكسس',       26),
    ('retained',      'close', 'الأرباح المحتجزة',          'equity',    'retained',      'قيد الإقفال السنوي',          27),
    ('capital',       'close', 'رأس المال',                 'equity',    'capital',       'قيد رأس المال',               28),
    ('depr',          'close', 'مجمع الإهلاك',              'asset',     'depr',          'قيد الإهلاك (10ب)',           29);

-- الحساب أول مرة (تجهيز مش تعديل من حد — الإصدار بيفضل 1): بالدور، والمفتاحين اللي مالهمش دور
-- بالحساب الجاهز في الدليل المصري (4102 إيرادات تركيبات وخدمات · 5402 مصاريف بنكية) لو لسه فرعي نشط.
ALTER TABLE acc.default_accounts DISABLE TRIGGER zz_row_version_trg;
UPDATE acc.default_accounts d SET account_id = (
    SELECT a.id FROM acc.accounts a
    WHERE a.is_leaf AND a.is_active AND a.account_type = d.account_type
      AND (a.role_code = d.role_code
           OR (d.role_code IS NULL AND a.code = CASE d.key WHEN 'services' THEN '4102' WHEN 'bank_charges' THEN '5402' END))
    ORDER BY a.code LIMIT 1);
ALTER TABLE acc.default_accounts ENABLE TRIGGER zz_row_version_trg;
