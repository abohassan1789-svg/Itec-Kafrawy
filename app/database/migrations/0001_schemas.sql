-- 0001: إنشاء الـ schemas بتاعة البرنامج (الخطة — القسم 10.1)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.

CREATE SCHEMA IF NOT EXISTS sec;
COMMENT ON SCHEMA sec   IS 'الأمان: المستخدمين والمجموعات والصلاحيات وسجل الدخول وسجل التعديلات (من Hr GAD)';

CREATE SCHEMA IF NOT EXISTS core;
COMMENT ON SCHEMA core  IS 'الإعدادات العامة: الشركة، السنوات المالية، العملات وأسعار الصرف، ترقيم المستندات';

CREATE SCHEMA IF NOT EXISTS acc;
COMMENT ON SCHEMA acc   IS 'الحسابات: دليل الحسابات، مراكز التكلفة، المشاريع/أوامر التوريد، القيود، قواعد الترحيل';

CREATE SCHEMA IF NOT EXISTS party;
COMMENT ON SCHEMA party IS 'الأطراف: العملاء، الموردين، الموظفين، الشركاء، أصحاب العمولات';

CREATE SCHEMA IF NOT EXISTS inv;
COMMENT ON SCHEMA inv   IS 'المخازن: الأصناف وأنواعها، المخازن، دفتر حركة المخزون، التحويلات، الجرد';

CREATE SCHEMA IF NOT EXISTS sales;
COMMENT ON SCHEMA sales IS 'المبيعات: إذن الصرف، المرتجع، الصرف الوهمي';

CREATE SCHEMA IF NOT EXISTS purch;
COMMENT ON SCHEMA purch IS 'المشتريات: فواتير الشراء والمرتجعات والمصاريف الإضافية';

CREATE SCHEMA IF NOT EXISTS cash;
COMMENT ON SCHEMA cash  IS 'الخزينة والبنوك: الخزائن، البنوك، السندات وتوزيعها، الشيكات، العهد';

CREATE SCHEMA IF NOT EXISTS exp;
COMMENT ON SCHEMA exp   IS 'المصروفات: أنواع المصروفات، المصروفات وتوزيعها على المشاريع';

-- core.app_info: معلومات ثابتة عن القاعدة (مين عملها وإمتى ولأنهي برنامج)
CREATE TABLE IF NOT EXISTS core.app_info (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE core.app_info IS 'معلومات عن القاعدة والبرنامج';

INSERT INTO core.app_info (key, value) VALUES
    ('app_name', 'A-H CODE — ITEC Accounting'),
    ('created_at', now()::text),
    ('base_currency', 'EGP')
ON CONFLICT (key) DO NOTHING;
