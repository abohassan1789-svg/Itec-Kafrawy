-- 0032: محرك الترحيل المركزي — «قواعد الترحيل» (المرحلة 4 — من غير شاشة).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد (القواعد نفسها بيانات — تتعدّل بـ UPDATE في سكريبت جديد).
--
-- الفكرة (من E3marEG — ملف 03 البند 3 و8 و9، مع تصليح عيوبه):
--   * كل مستند (فاتورة / سند / تسوية …) **مابيكتبش حسابات** — بيقول «السطر ده دوره إيه ومبلغه كام»
--     (مثلاً فاتورة البيع: العميل 1,140 · المبيعات 1,000 · ض.ق.م 140)، والمحرك (app/services/posting_service.py)
--     بيجيب الحساب من القاعدة دي، ويقرّب، ويتأكد من التوازن، ويعمل القيد ويرحّله **في نفس الـ transaction بتاع المستند**.
--   * **مصدر الحساب** (account_source):
--       default      ← مفتاح في «الحسابات الافتراضية» (default_key)
--       category     ← نوع الصنف وأقرب أب عنده الحساب ← وبعدين الافتراضي بنفس المفتاح (inv.item_category_account — 0028).
--                      ترتيب البحث في الخطة «الصنف ← النوع ← المخزن ← الافتراضي»: **الصنف مالوش حساب** (قرار 0029)
--                      و**المخازن كلها على حساب واحد + بُعد المخزن** (قرار المستخدم) ← فالترتيب الفعلي = النوع ← الافتراضي.
--       party        ← حساب الرقابة بتاع مجموعة العميل / المورد (والموظف / الشريك ← default_key) + بُعد الطرف لوحده.
--       tax_sales    ← حساب «مبيعات» كود الضريبة ← وإلا default_key (ض.ق.م مخرجات).
--       tax_purchase ← حساب «مشتريات» كود الضريبة ← وإلا default_key (ض.ق.م مدخلات).
--       treasury     ← حساب الخزنة · bank ← حساب الحساب البنكي · expense ← حساب نوع المصروف.
--       given        ← المستند بيدّي الحساب بنفسه (الأرصدة الافتتاحية · «حساب تاني» في السندات).
--   * **side** = الجنب الطبيعي للدور (مدين / دائن). **المبلغ بالسالب بيقلب الجنب** (عجز الجرد · رصيد افتتاحي دائن).
--   * القيد الأوتوماتيك **من غير رقم JV** (بيتعرض برقم المستند `source_no`) — عشان إلغاء ترحيل المستند بيمسح قيده،
--     والأرقام الرسمية للقيود اليدوية ماتعملش فجوة.
--   * أي حاجة ناقصة (حساب افتراضي مش متحدد · طرف ناقص · حساب موقوف · مش متوازن) ← **خطأ بالعربي والمستند مايترحّلش**
--     (المحرك عمره ما يسكت — عكس E3marEG).

-- ---------------------------------------------------------------------------
-- 1) أنواع المستندات اللي بتعمل قيود (source_type في القيود)
-- ---------------------------------------------------------------------------
CREATE TABLE acc.posting_doc_types (
    code        varchar(30)  PRIMARY KEY,
    name_ar     varchar(60)  NOT NULL,
    sort_order  smallint     NOT NULL,
    CONSTRAINT posting_doc_types_code_chk CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT posting_doc_types_name_chk CHECK (btrim(name_ar) <> '')
);
COMMENT ON TABLE acc.posting_doc_types IS 'أنواع المستندات اللي بتعمل قيود (manual = القيد اليدوي)';

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('manual',           'قيد يدوي',          1),
    ('opening_balance',  'أرصدة افتتاحية',    2),
    ('sales_invoice',    'فاتورة بيع',        3),
    ('sales_return',     'مردود بيع',         4),
    ('purchase_invoice', 'فاتورة شراء',       5),
    ('purchase_return',  'مردود شراء',        6),
    ('receipt',          'سند قبض',           7),
    ('payment',          'سند صرف',           8),
    ('stock_adjustment', 'تسوية جرد',         9);

-- نوع المستند في القيد لازم يبقى معروف · والقيد الأوتوماتيك لازم معاه رقم المستند (بيتعرض بيه).
ALTER TABLE acc.journal_entries
    ADD CONSTRAINT journal_entries_source_type_fk FOREIGN KEY (source_type)
        REFERENCES acc.posting_doc_types (code) ON DELETE RESTRICT,
    ADD CONSTRAINT journal_entries_source_no_chk CHECK (source_type = 'manual' OR btrim(source_no) <> '');

-- ---------------------------------------------------------------------------
-- 2) قواعد الترحيل: (نوع المستند، دور السطر) ← الجنب + مصدر الحساب
-- ---------------------------------------------------------------------------
CREATE TABLE acc.posting_rules (
    doc_type        varchar(30)  NOT NULL REFERENCES acc.posting_doc_types (code) ON DELETE CASCADE,
    role            varchar(30)  NOT NULL,
    name_ar         varchar(60)  NOT NULL,
    side            char(2)      NOT NULL,
    account_source  varchar(15)  NOT NULL,
    default_key     varchar(30)  REFERENCES acc.default_accounts (key) ON DELETE RESTRICT,
    category_kind   varchar(10),
    party_kind      varchar(10),
    sort_order      smallint     NOT NULL,
    PRIMARY KEY (doc_type, role),
    CONSTRAINT posting_rules_role_chk CHECK (role ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT posting_rules_name_chk CHECK (btrim(name_ar) <> ''),
    CONSTRAINT posting_rules_side_chk CHECK (side IN ('dr', 'cr')),
    CONSTRAINT posting_rules_source_chk CHECK (account_source IN
        ('default', 'category', 'party', 'tax_sales', 'tax_purchase', 'treasury', 'bank', 'expense', 'given')),
    CONSTRAINT posting_rules_party_kind_chk CHECK (party_kind IS NULL OR party_kind IN
        ('customer', 'supplier', 'employee', 'partner')),
    CONSTRAINT posting_rules_category_chk CHECK ((account_source = 'category') = (category_kind IS NOT NULL)
        AND (category_kind IS NULL OR category_kind IN ('inventory', 'sales', 'cogs', 'sales_ret'))),
    -- الافتراضي لازم مفتاح · الضريبة لازم مفتاح احتياطي · الطرف لازم نوعه (والموظف/الشريك لازم مفتاح).
    CONSTRAINT posting_rules_key_chk CHECK (
        (account_source NOT IN ('default', 'tax_sales', 'tax_purchase') OR default_key IS NOT NULL)
        AND (account_source <> 'party' OR (party_kind IS NOT NULL
             AND (party_kind IN ('customer', 'supplier') OR default_key IS NOT NULL)))
        AND (account_source NOT IN ('treasury', 'bank', 'expense', 'given') OR default_key IS NULL))
);
CREATE INDEX posting_rules_default_key_idx ON acc.posting_rules (default_key);
COMMENT ON TABLE acc.posting_rules IS
    'قواعد الترحيل: (نوع المستند، دور السطر) ← الجنب الطبيعي + مصدر الحساب (افتراضي / نوع الصنف / الطرف / الضريبة / خزنة / بنك / مصروف / من المستند)';

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    -- الأرصدة الافتتاحية: كل حساب برصيده (السالب = دائن) ← وعكسه على «الأرصدة الافتتاحية» (حقوق ملكية وسيط).
    ('opening_balance',  'line',            'رصيد الحساب',         'dr', 'given',        NULL,            NULL,        NULL,       1),
    ('opening_balance',  'opening',         'الأرصدة الافتتاحية',  'cr', 'default',      'opening',       NULL,        NULL,       2),
    -- فاتورة البيع: قيد واحد (إيراد + ض.ق.م + تكلفة) — قرار المستخدم.
    ('sales_invoice',    'receivable',      'العملاء',             'dr', 'party',        NULL,            NULL,        'customer', 1),
    ('sales_invoice',    'discount',        'خصم مسموح به',        'dr', 'default',      'disc_allowed',  NULL,        NULL,       2),
    ('sales_invoice',    'revenue',         'المبيعات',            'cr', 'category',     NULL,            'sales',     NULL,       3),
    ('sales_invoice',    'service_revenue', 'إيراد الخدمات',       'cr', 'default',      'services',      NULL,        NULL,       4),
    ('sales_invoice',    'vat',             'ض.ق.م مخرجات',        'cr', 'tax_sales',    'vat_out',       NULL,        NULL,       5),
    ('sales_invoice',    'cogs',            'تكلفة المبيعات',      'dr', 'category',     NULL,            'cogs',      NULL,       6),
    ('sales_invoice',    'inventory',       'المخزون',             'cr', 'category',     NULL,            'inventory', NULL,       7),
    -- مردود البيع: عكس الفاتورة على «مردودات المبيعات».
    ('sales_return',     'sales_return',    'مردودات المبيعات',    'dr', 'category',     NULL,            'sales_ret', NULL,       1),
    ('sales_return',     'vat',             'ض.ق.م مخرجات',        'dr', 'tax_sales',    'vat_out',       NULL,        NULL,       2),
    ('sales_return',     'discount',        'خصم مسموح به',        'cr', 'default',      'disc_allowed',  NULL,        NULL,       3),
    ('sales_return',     'receivable',      'العملاء',             'cr', 'party',        NULL,            NULL,        'customer', 4),
    ('sales_return',     'inventory',       'المخزون',             'dr', 'category',     NULL,            'inventory', NULL,       5),
    ('sales_return',     'cogs',            'تكلفة المبيعات',      'cr', 'category',     NULL,            'cogs',      NULL,       6),
    -- فاتورة الشراء.
    ('purchase_invoice', 'inventory',       'المخزون',             'dr', 'category',     NULL,            'inventory', NULL,       1),
    ('purchase_invoice', 'expense',         'المصروف',             'dr', 'expense',      NULL,            NULL,        NULL,       2),
    ('purchase_invoice', 'vat',             'ض.ق.م مدخلات',        'dr', 'tax_purchase', 'vat_in',        NULL,        NULL,       3),
    ('purchase_invoice', 'payable',         'الموردين',            'cr', 'party',        NULL,            NULL,        'supplier', 4),
    ('purchase_invoice', 'discount',        'خصم مكتسب',           'cr', 'default',      'disc_received', NULL,        NULL,       5),
    -- مردود الشراء: عكس الفاتورة.
    ('purchase_return',  'payable',         'الموردين',            'dr', 'party',        NULL,            NULL,        'supplier', 1),
    ('purchase_return',  'discount',        'خصم مكتسب',           'dr', 'default',      'disc_received', NULL,        NULL,       2),
    ('purchase_return',  'inventory',       'المخزون',             'cr', 'category',     NULL,            'inventory', NULL,       3),
    ('purchase_return',  'expense',         'المصروف',             'cr', 'expense',      NULL,            NULL,        NULL,       4),
    ('purchase_return',  'vat',             'ض.ق.م مدخلات',        'cr', 'tax_purchase', 'vat_in',        NULL,        NULL,       5),
    -- سند القبض.
    ('receipt',          'cash',            'الخزينة',             'dr', 'treasury',     NULL,            NULL,        NULL,       1),
    ('receipt',          'bank',            'البنك',               'dr', 'bank',         NULL,            NULL,        NULL,       2),
    ('receipt',          'wht',             'خصم من المنبع (لينا)', 'dr', 'default',     'wht_rec',       NULL,        NULL,       3),
    ('receipt',          'discount',        'خصم مسموح به',        'dr', 'default',      'disc_allowed',  NULL,        NULL,       4),
    ('receipt',          'receivable',      'العملاء',             'cr', 'party',        NULL,            NULL,        'customer', 5),
    ('receipt',          'advance',         'دفعات مقدمة من العملاء', 'cr', 'default',   'cust_adv',      NULL,        NULL,       6),
    ('receipt',          'other',           'حساب تاني',           'cr', 'given',        NULL,            NULL,        NULL,       7),
    -- سند الصرف.
    ('payment',          'payable',         'الموردين',            'dr', 'party',        NULL,            NULL,        'supplier', 1),
    ('payment',          'expense',         'المصروف',             'dr', 'expense',      NULL,            NULL,        NULL,       2),
    ('payment',          'other',           'حساب تاني',           'dr', 'given',        NULL,            NULL,        NULL,       3),
    ('payment',          'cash',            'الخزينة',             'cr', 'treasury',     NULL,            NULL,        NULL,       4),
    ('payment',          'bank',            'البنك',               'cr', 'bank',         NULL,            NULL,        NULL,       5),
    ('payment',          'wht',             'خصم من المنبع (علينا)', 'cr', 'default',    'wht_pay',       NULL,        NULL,       6),
    ('payment',          'discount',        'خصم مكتسب',           'cr', 'default',      'disc_received', NULL,        NULL,       7),
    -- تسوية الجرد: الزيادة بالموجب (المخزون مدين) والعجز بالسالب (المخزون دائن).
    ('stock_adjustment', 'inventory',       'المخزون',             'dr', 'category',     NULL,            'inventory', NULL,       1),
    ('stock_adjustment', 'adjustment',      'تسوية الجرد',         'cr', 'default',      'inv_adj',       NULL,        NULL,       2);
