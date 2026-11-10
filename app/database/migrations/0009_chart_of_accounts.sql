-- 0009: شجرة الحسابات (المرحلة 2 — شاشة «شجرة الحسابات»، النموذج 6 «تبويب لكل نوع»)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- الفكرة (docs/01 القسم 10.1 + docs/03 القسم 3.1 و6 و8):
--   * شجرة: كل حساب تحت أب (parent_id)، والجذور الخمسة: 1 أصول · 2 خصوم · 3 حقوق ملكية · 4 إيرادات · 5 مصروفات.
--   * الكود أرقام بس، مش متكرر، **بيبدأ بكود الأب وأطول منه** (1 ← 11 ← 1103 ← 110301) — لحد 5 مستويات.
--   * رئيسي (تجميعي — مابيقبلش قيود) / فرعي (is_leaf — بيقبل قيود).
--   * النوع بيتورث من الجذر، والقائمة (ميزانية / دخل) بتتحسب من النوع — مابيتكتبوش بإيد.
--   * التقارير بتتصنّف من **حقول** (التصنيف + بند التدفقات) مش من أول رقم في الكود.
--   * الدور (account_roles): البرنامج بيعرف منه أنهي حساب يستخدم في المستندات — من غير بحث بالاسم أبداً.
--   * العملاء والموردين = **حساب رقابة واحد + بُعد الطرف** (party_kind = لازم طرف مع القيد) — مش حساب لكل عميل.
--   * «نظامي» = حساب البرنامج محتاجه: مايتمسحش، ومايتوقفش، وكوده ومكانه ودوره ونوعه (رئيسي/فرعي) ثابتين.
-- لسه (مع القيود — المرحلة 4): منع مسح حساب عليه قيود، ومنع تحويل حساب عليه قيود لرئيسي، والقيد على فرعي نشط بس.

-- ---------------------------------------------------------------------------
-- أدوار الحسابات
-- ---------------------------------------------------------------------------
CREATE TABLE acc.account_roles (
    code                varchar(20)  PRIMARY KEY,
    name_ar             varchar(60)  NOT NULL,
    name_en             varchar(60),
    account_type        varchar(10),                 -- النوع اللي الدور بيتحط عليه (NULL = أي نوع)
    default_party_kind  varchar(10),                 -- الطرف اللي بيبقى لازم مع القيد افتراضياً
    sort_order          smallint     NOT NULL,
    CONSTRAINT account_roles_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,19}$'),
    CONSTRAINT account_roles_type_chk CHECK (account_type IS NULL OR account_type IN
        ('asset', 'liability', 'equity', 'revenue', 'expense')),
    CONSTRAINT account_roles_party_chk CHECK (default_party_kind IS NULL OR default_party_kind IN
        ('customer', 'supplier', 'employee', 'partner'))
);
COMMENT ON TABLE acc.account_roles IS 'أدوار الحسابات: البرنامج بيعرف منها حساب الخزينة والعملاء وض.ق.م… (من غير بحث بالاسم)';

INSERT INTO acc.account_roles (code, name_ar, name_en, account_type, default_party_kind, sort_order) VALUES
    ('cash',       'خزينة',              'Cash Box',                 'asset',     NULL,       1),
    ('bank',       'بنك',                'Bank',                     'asset',     NULL,       2),
    ('customers',  'العملاء',            'Customers',                'asset',     'customer', 3),
    ('suppliers',  'الموردين',           'Suppliers',                'liability', 'supplier', 4),
    ('notes_rec',  'أوراق قبض',          'Notes Receivable',         'asset',     'customer', 5),
    ('notes_pay',  'أوراق دفع',          'Notes Payable',            'liability', 'supplier', 6),
    ('inventory',  'المخزون',            'Inventory',                'asset',     NULL,       7),
    ('vat_in',     'ض.ق.م مدخلات',       'VAT Input',                'asset',     NULL,       8),
    ('vat_out',    'ض.ق.م مخرجات',       'VAT Output',               'liability', NULL,       9),
    ('wht_rec',    'خصم منبع لينا',      'WHT Receivable',           'asset',     NULL,      10),
    ('wht_pay',    'خصم منبع علينا',     'WHT Payable',              'liability', NULL,      11),
    ('custody',    'عهد',                'Employee Custody',         'asset',     'employee',12),
    ('advances',   'دفعات مقدمة',        'Advances',                 NULL,        'customer',13),
    ('capital',    'رأس المال',          'Capital',                  'equity',    NULL,      14),
    ('partner',    'جاري شريك',          'Partner Current',          'equity',    'partner', 15),
    ('retained',   'أرباح محتجزة',       'Retained Earnings',        'equity',    NULL,      16),
    ('opening',    'أرصدة افتتاحية',     'Opening Balances',         'equity',    NULL,      17),
    ('sales',      'المبيعات',           'Sales',                    'revenue',   NULL,      18),
    ('sales_ret',  'مردودات مبيعات',     'Sales Returns',            'revenue',   NULL,      19),
    ('cogs',       'تكلفة المبيعات',     'Cost of Sales',            'expense',   NULL,      20),
    ('fx_gain',    'فروق عملة +',        'FX Gains',                 'revenue',   NULL,      21),
    ('fx_loss',    'فروق عملة −',        'FX Losses',                'expense',   NULL,      22),
    ('depr',       'مجمع إهلاك',         'Accumulated Depreciation', 'asset',     NULL,      23);

-- ---------------------------------------------------------------------------
-- الحسابات
-- ---------------------------------------------------------------------------
CREATE TABLE acc.accounts (
    id                    integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                  varchar(20)  NOT NULL,
    name_ar               varchar(120) NOT NULL,
    name_en               varchar(120),
    parent_id             integer      REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    level                 smallint     NOT NULL DEFAULT 1,          -- بيتحسب (trigger)
    is_leaf               boolean      NOT NULL DEFAULT true,       -- فرعي = بيقبل قيود · رئيسي = تجميعي
    account_type          varchar(10)  NOT NULL,                    -- بيتورث من الجذر (trigger)
    nature                char(2)      NOT NULL,                    -- dr مدين · cr دائن
    statement             char(2)      NOT NULL DEFAULT 'bs',       -- bs ميزانية · is دخل (بتتحسب من النوع)
    classification        varchar(20)  NOT NULL,
    cash_flow             varchar(4)   NOT NULL DEFAULT 'none',     -- op · inv · fin · cash · none
    role_code             varchar(20)  REFERENCES acc.account_roles (code) ON DELETE RESTRICT,
    party_kind            varchar(10),                              -- لازم طرف مع القيد (NULL = لأ)
    requires_cost_center  boolean      NOT NULL DEFAULT false,
    currency_code         char(3)      NOT NULL DEFAULT 'EGP' REFERENCES core.currencies (code),
    is_active             boolean      NOT NULL DEFAULT true,
    is_system             boolean      NOT NULL DEFAULT false,
    created_at            timestamptz  NOT NULL DEFAULT now(),
    updated_at            timestamptz  NOT NULL DEFAULT now(),
    row_version           integer      NOT NULL DEFAULT 1 CONSTRAINT accounts_row_version_chk CHECK (row_version > 0),
    CONSTRAINT accounts_code_unique UNIQUE (code),
    CONSTRAINT accounts_code_format CHECK (code ~ '^[0-9]{1,20}$'),
    CONSTRAINT accounts_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT accounts_level_range CHECK (level BETWEEN 1 AND 5),
    CONSTRAINT accounts_type_chk CHECK (account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
    CONSTRAINT accounts_nature_chk CHECK (nature IN ('dr', 'cr')),
    CONSTRAINT accounts_statement_chk CHECK (statement =
        CASE WHEN account_type IN ('asset', 'liability', 'equity') THEN 'bs' ELSE 'is' END),
    -- التصنيف لازم يناسب النوع.
    CONSTRAINT accounts_classification_chk CHECK (
        (account_type IN ('asset', 'liability') AND classification IN ('current', 'noncurrent'))
     OR (account_type = 'equity'  AND classification = 'equity')
     OR (account_type = 'revenue' AND classification IN ('operating_rev', 'other_rev', 'returns'))
     OR (account_type = 'expense' AND classification IN ('cogs', 'operating_exp', 'finance_exp', 'other_exp'))),
    CONSTRAINT accounts_cash_flow_chk CHECK (cash_flow IN ('op', 'inv', 'fin', 'cash', 'none')),
    CONSTRAINT accounts_party_chk CHECK (party_kind IS NULL OR party_kind IN
        ('customer', 'supplier', 'employee', 'partner')),
    CONSTRAINT accounts_root_chk CHECK ((parent_id IS NULL) = (level = 1))
);
CREATE INDEX accounts_parent_idx ON acc.accounts (parent_id);
CREATE INDEX accounts_role_idx ON acc.accounts (role_code);
CREATE INDEX accounts_currency_idx ON acc.accounts (currency_code);
CREATE INDEX accounts_type_idx ON acc.accounts (account_type, code);
CREATE INDEX accounts_name_idx ON acc.accounts (name_ar);
COMMENT ON TABLE acc.accounts IS 'دليل الحسابات (شجرة) — القيود على الحسابات الفرعية النشطة بس';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p          acc.accounts%ROWTYPE;
    has_kids   boolean := false;
    role_type  varchar(10);
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.level := 1;
        IF NEW.account_type IS NULL THEN
            RAISE EXCEPTION 'الحساب الجذر «%» لازم يتحدد نوعه.', NEW.code;
        END IF;
    ELSE
        -- FOR SHARE: لو جهاز تاني بيحوّل الأب فرعي أو بيوقفه في نفس اللحظة، واحد يستنى التاني.
        SELECT * INTO p FROM acc.accounts WHERE id = NEW.parent_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الحساب الأب مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF p.id = NEW.id THEN
            RAISE EXCEPTION 'الحساب مينفعش يبقى تحت نفسه.';
        END IF;
        IF p.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» فرعي — مينفعش يبقى تحته حسابات (حوّله رئيسي الأول).', p.code, p.name_ar;
        END IF;
        -- تحت أب موقوف: مينفعش تضيف ولا تنقل ولا تنشّط (تعديل حساب موقوف تحت أب موقوف عادي).
        IF NOT p.is_active AND (TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                                OR (NEW.is_active AND NOT OLD.is_active)) THEN
            RAISE EXCEPTION 'الحساب «% %» موقوف — مينفعش يبقى تحته حسابات نشطة أو جديدة.', p.code, p.name_ar;
        END IF;
        IF left(NEW.code, length(p.code)) <> p.code OR length(NEW.code) <= length(p.code) THEN
            RAISE EXCEPTION 'كود الحساب لازم يبدأ بكود الأب «%» ويبقى أطول منه.', p.code;
        END IF;
        NEW.level := p.level + 1;
        IF NEW.level > 5 THEN
            RAISE EXCEPTION 'أقصى عدد مستويات في الدليل 5 — مينفعش تضيف تحت «%».', p.code;
        END IF;
        NEW.account_type := p.account_type;
    END IF;
    IF NEW.level = 5 AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'المستوى الخامس آخر مستوى — الحساب «%» لازم يبقى فرعي.', NEW.code;
    END IF;
    NEW.statement := CASE WHEN NEW.account_type IN ('asset', 'liability', 'equity') THEN 'bs' ELSE 'is' END;

    IF NEW.role_code IS NOT NULL THEN
        SELECT account_type INTO role_type FROM acc.account_roles WHERE code = NEW.role_code;
        IF role_type IS NOT NULL AND role_type <> NEW.account_type THEN
            RAISE EXCEPTION 'الدور ده مايتحطش على حساب من النوع ده (الحساب «%»).', NEW.code;
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        has_kids := EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = OLD.id);
        IF OLD.is_system AND (NEW.code <> OLD.code OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                              OR NEW.role_code IS DISTINCT FROM OLD.role_code OR NEW.is_leaf <> OLD.is_leaf
                              OR NEW.account_type <> OLD.account_type OR NOT NEW.is_system) THEN
            RAISE EXCEPTION 'الحساب «% %» نظامي — كوده ومكانه ودوره ونوعه ثابتين.', OLD.code, OLD.name_ar;
        END IF;
        IF OLD.is_system AND NOT NEW.is_active THEN
            RAISE EXCEPTION 'الحساب «% %» نظامي — مايتوقفش (البرنامج محتاجه).', OLD.code, OLD.name_ar;
        END IF;
        IF NEW.is_system AND NOT OLD.is_system THEN
            RAISE EXCEPTION 'مينفعش تخلّي حساب نظامي بإيدك.';
        END IF;
        IF has_kids AND (NEW.code <> OLD.code OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                         OR NEW.account_type <> OLD.account_type) THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات — مينفعش تغيّر كوده ولا مكانه ولا نوعه.', OLD.code, OLD.name_ar;
        END IF;
        IF has_kids AND NEW.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات — مينفعش يتحوّل فرعي.', OLD.code, OLD.name_ar;
        END IF;
        IF OLD.is_active AND NOT NEW.is_active
           AND EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = OLD.id AND is_active) THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات نشطة — وقّفهم الأول.', OLD.code, OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_guard_trg
    BEFORE INSERT OR UPDATE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_guard();

-- الحماية (مسح): النظامي مايتمسحش، واللي تحته حسابات مايتمسحش (رسالة عربي بدل خطأ الـ FK).
CREATE FUNCTION acc.accounts_delete_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_system THEN
        RAISE EXCEPTION 'الحساب «% %» نظامي — مايتمسحش.', OLD.code, OLD.name_ar;
    END IF;
    IF EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = OLD.id) THEN
        RAISE EXCEPTION 'الحساب «% %» تحته حسابات — امسحهم الأول.', OLD.code, OLD.name_ar;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER accounts_delete_guard_trg
    BEFORE DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_delete_guard();

-- حماية التعديل في نفس الوقت (المرحلة 1ج — زي 0008).
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- الدليل المصري المقترح (بيتسجل أول مرة — والمستخدم يعدّل فيه)
-- نظامي = الجذور الخمسة + الحسابات اللي ليها دور (أول حساب بالدور في فرعه) + المجموعات الأساسية.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
         -- الكود، الاسم، الإنجليزي، الأب، فرعي؟، الطبيعة، التصنيف، التدفقات، الدور، الطرف، مركز تكلفة، نظامي، نشط
         ('1','الأصول','Assets',NULL,false,'dr','current','none',NULL,NULL,false,true,true),
         ('11','الأصول المتداولة','Current Assets','1',false,'dr','current','op',NULL,NULL,false,true,true),
         ('1101','النقدية بالخزائن','Cash on Hand','11',false,'dr','current','cash','cash',NULL,false,true,true),
         ('110101','الخزينة الرئيسية','Main Cash Box','1101',true,'dr','current','cash','cash',NULL,false,false,true),
         ('110102','خزينة النثريات','Petty Cash','1101',true,'dr','current','cash','cash',NULL,false,false,true),
         ('1102','البنوك','Banks','11',false,'dr','current','cash','bank',NULL,false,true,true),
         ('110201','البنك الأهلي — جاري','NBE Current','1102',true,'dr','current','cash','bank',NULL,false,false,true),
         ('110202','بنك مصر — جاري','Banque Misr Current','1102',true,'dr','current','cash','bank',NULL,false,false,true),
         ('1103','العملاء','Customers','11',true,'dr','current','op','customers','customer',false,true,true),
         ('1104','أوراق القبض (شيكات)','Notes Receivable','11',true,'dr','current','op','notes_rec','customer',false,true,true),
         ('1105','المخزون','Inventory','11',true,'dr','current','op','inventory',NULL,false,true,true),
         ('1106','ضرايب لينا','Tax Receivables','11',false,'dr','current','op',NULL,NULL,false,false,true),
         ('110601','ض.ق.م — مدخلات','VAT Input','1106',true,'dr','current','op','vat_in',NULL,false,true,true),
         ('110602','خصم من المنبع (لينا)','WHT Receivable','1106',true,'dr','current','op','wht_rec',NULL,false,true,true),
         ('1107','عهد الموظفين','Employee Custody','11',true,'dr','current','op','custody','employee',false,true,true),
         ('12','الأصول الثابتة','Fixed Assets','1',false,'dr','noncurrent','inv',NULL,NULL,false,true,true),
         ('1201','أجهزة وحاسبات','Computers','12',true,'dr','noncurrent','inv',NULL,NULL,false,false,true),
         ('1202','سيارات','Vehicles','12',true,'dr','noncurrent','inv',NULL,NULL,false,false,true),
         ('1209','مجمع الإهلاك','Accumulated Depreciation','12',true,'cr','noncurrent','op','depr',NULL,false,true,true),
         ('2','الخصوم','Liabilities',NULL,false,'cr','current','none',NULL,NULL,false,true,true),
         ('21','الخصوم المتداولة','Current Liabilities','2',false,'cr','current','op',NULL,NULL,false,true,true),
         ('2101','الموردين','Suppliers','21',true,'cr','current','op','suppliers','supplier',false,true,true),
         ('2102','أوراق الدفع (شيكات)','Notes Payable','21',true,'cr','current','op','notes_pay','supplier',false,true,true),
         ('2103','ض.ق.م — مخرجات','VAT Output','21',true,'cr','current','op','vat_out',NULL,false,true,true),
         ('2104','خصم من المنبع (مستحق)','WHT Payable','21',true,'cr','current','op','wht_pay',NULL,false,true,true),
         ('2105','دفعات مقدمة من العملاء','Customer Advances','21',true,'cr','current','op','advances','customer',false,true,true),
         ('3','حقوق الملكية','Equity',NULL,false,'cr','equity','fin',NULL,NULL,false,true,true),
         ('31','رأس المال','Capital','3',true,'cr','equity','fin','capital',NULL,false,true,true),
         ('32','جاري الشركاء','Partners Current','3',true,'cr','equity','fin','partner','partner',false,true,true),
         ('33','الأرباح المحتجزة','Retained Earnings','3',true,'cr','equity','none','retained',NULL,false,true,true),
         ('34','أرصدة افتتاحية','Opening Balances','3',true,'cr','equity','none','opening',NULL,false,true,true),
         ('4','الإيرادات','Revenues',NULL,false,'cr','operating_rev','op',NULL,NULL,false,true,true),
         ('41','المبيعات','Sales','4',false,'cr','operating_rev','op',NULL,NULL,false,true,true),
         ('4101','مبيعات بضاعة','Goods Sales','41',true,'cr','operating_rev','op','sales',NULL,true,true,true),
         ('4102','إيرادات تركيبات وخدمات','Installation & Services','41',true,'cr','operating_rev','op',NULL,NULL,true,false,true),
         ('42','مردودات المبيعات','Sales Returns','4',true,'dr','returns','op','sales_ret',NULL,true,true,true),
         ('43','إيرادات أخرى','Other Revenues','4',false,'cr','other_rev','op',NULL,NULL,false,false,true),
         ('4301','فروق عملة دائنة','FX Gains','43',true,'cr','other_rev','op','fx_gain',NULL,false,true,true),
         ('5','المصروفات والتكاليف','Expenses',NULL,false,'dr','operating_exp','op',NULL,NULL,false,true,true),
         ('51','تكلفة المبيعات','Cost of Sales','5',true,'dr','cogs','op','cogs',NULL,true,true,true),
         ('52','مصروفات عمومية وإدارية','G&A Expenses','5',false,'dr','operating_exp','op',NULL,NULL,false,false,true),
         ('5201','المرتبات والأجور','Salaries','52',true,'dr','operating_exp','op',NULL,NULL,true,false,true),
         ('5202','الإيجار','Rent','52',true,'dr','operating_exp','op',NULL,NULL,false,false,true),
         ('5203','كهرباء ومياه','Utilities','52',true,'dr','operating_exp','op',NULL,NULL,false,false,true),
         ('5204','مصروفات نثرية','Miscellaneous','52',true,'dr','operating_exp','op',NULL,NULL,false,false,true),
         ('53','مصروفات بيع وتوزيع','Selling Expenses','5',false,'dr','operating_exp','op',NULL,NULL,false,false,true),
         ('5301','نقل ومشال','Freight','53',true,'dr','operating_exp','op',NULL,NULL,true,false,true),
         ('5302','عمولات بيع','Sales Commissions','53',true,'dr','operating_exp','op',NULL,NULL,true,false,false),
         ('54','مصروفات تمويلية','Finance Costs','5',false,'dr','finance_exp','fin',NULL,NULL,false,false,true),
         ('5401','فروق عملة مدينة','FX Losses','54',true,'dr','finance_exp','op','fx_loss',NULL,false,true,true),
         ('5402','مصاريف بنكية','Bank Charges','54',true,'dr','finance_exp','op',NULL,NULL,false,false,true)
        ) AS t (code, name_ar, name_en, parent_code, is_leaf, nature, classification, cash_flow, role_code,
                party_kind, requires_cost_center, is_system, is_active)
        ORDER BY length(code), code
    LOOP
        INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                                  cash_flow, role_code, party_kind, requires_cost_center, is_system, is_active)
        VALUES (r.code, r.name_ar, r.name_en,
                (SELECT id FROM acc.accounts WHERE code = r.parent_code),
                r.is_leaf,
                CASE left(r.code, 1) WHEN '1' THEN 'asset' WHEN '2' THEN 'liability' WHEN '3' THEN 'equity'
                                     WHEN '4' THEN 'revenue' ELSE 'expense' END,
                r.nature, r.classification, r.cash_flow, r.role_code, r.party_kind, r.requires_cost_center,
                r.is_system, r.is_active);
    END LOOP;
END $$;
