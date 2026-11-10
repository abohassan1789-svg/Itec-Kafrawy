-- 0003: بيانات الشركة (المرحلة 1ب — شاشة «بيانات الشركة»، النموذج 1)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- سجل واحد بس للشركة (id = 1 دايماً). بيحل محل جدول sd في الأكسس.
-- البيانات دي بتطلع في هيدر كل فاتورة وتقرير.
-- العملة الأساسية EGP (قرار المستخدم). هتتربط بـ core.currencies لما الجدول يتعمل،
-- وهتتقفل بعد أول قيد (Trigger في المرحلة 4).

CREATE TABLE core.company_settings (
    id                       smallint     PRIMARY KEY DEFAULT 1,
    name_ar                  varchar(200) NOT NULL,
    name_en                  varchar(200),
    activity                 varchar(200),                 -- النشاط
    address                  varchar(300),
    city                     varchar(100),
    phone                    varchar(40),
    mobile                   varchar(40),
    email                    varchar(120),
    website                  varchar(120),
    commercial_register      varchar(60),                  -- السجل التجاري (نص: ممكن فيه أصفار على الشمال)
    tax_registration_no      varchar(60),                  -- رقم التسجيل الضريبي
    tax_office               varchar(120),                 -- مأمورية الضرايب
    logo                     bytea,
    logo_mime                varchar(40),
    base_currency_code       char(3)      NOT NULL DEFAULT 'EGP',
    default_vat_rate         numeric(5,2) NOT NULL DEFAULT 14,
    fiscal_year_start_month  smallint     NOT NULL DEFAULT 1,
    invoice_footer           varchar(300),                 -- سطر تحت الفواتير
    print_logo               boolean      NOT NULL DEFAULT true,
    print_name_en            boolean      NOT NULL DEFAULT true,
    created_at               timestamptz  NOT NULL DEFAULT now(),
    updated_at               timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT company_settings_single_row CHECK (id = 1),
    CONSTRAINT company_settings_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT company_settings_currency_code CHECK (base_currency_code ~ '^[A-Z]{3}$'),
    CONSTRAINT company_settings_vat_range CHECK (default_vat_rate >= 0 AND default_vat_rate <= 100),
    CONSTRAINT company_settings_start_month CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
    CONSTRAINT company_settings_logo_size CHECK (logo IS NULL OR octet_length(logo) <= 2097152)
);
COMMENT ON TABLE core.company_settings IS 'بيانات الشركة — سجل واحد بس (id = 1)';
