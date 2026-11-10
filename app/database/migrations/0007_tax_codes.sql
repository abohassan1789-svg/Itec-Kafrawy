-- 0007: أكواد الضريبة (المرحلة 1ب — شاشة «أكواد الضريبة»، النموذج 1)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (2026-11-08):
--   * الفاتورة الواحدة ممكن يبقى فيها **أكتر من ضريبة** في جدول ضرايب لوحده.
--   * الضرايب **نوعين**: إضافة (بتزوّد الإجمالي — قيمة مضافة، جدول) وخصم (بتقلّله — الخصم من المنبع).
-- كل كود: النسبة، وبتتحسب على إيه (الصافي بس / الصافي + ضرايب الإضافة اللي ترتيبها قبلها)، والترتيب،
-- وبتتطبق على المبيعات أو المشتريات أو الاتنين، ونشطة / موقوفة، ومعفي.
-- حسابات الضريبة (مبيعات / مشتريات) بتتضاف لما يتعمل دليل الحسابات (المرحلة 2)،
-- ومنع مسح كود عليه فواتير بييجي مع الفواتير (FK RESTRICT).

CREATE TABLE acc.tax_codes (
    id           integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code         varchar(10)   NOT NULL,                     -- VAT14, WHT1…
    name_ar      varchar(60)   NOT NULL,
    kind         varchar(6)    NOT NULL DEFAULT 'add',       -- add = إضافة · deduct = خصم
    rate         numeric(7,4)  NOT NULL DEFAULT 0,           -- نسبة مئوية (14 = 14%)
    calc_base    varchar(4)    NOT NULL DEFAULT 'net',       -- net = الصافي · cum = الصافي + الإضافة اللي قبلها
    calc_order   smallint      NOT NULL DEFAULT 1,
    applies_to   varchar(5)    NOT NULL DEFAULT 'both',      -- both · sales · purch
    is_exempt    boolean       NOT NULL DEFAULT false,
    is_active    boolean       NOT NULL DEFAULT true,
    created_at   timestamptz   NOT NULL DEFAULT now(),
    updated_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT tax_codes_code_unique UNIQUE (code),
    CONSTRAINT tax_codes_code_format CHECK (code ~ '^[A-Z][A-Z0-9_]{0,9}$'),
    CONSTRAINT tax_codes_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT tax_codes_kind_chk CHECK (kind IN ('add', 'deduct')),
    CONSTRAINT tax_codes_rate_range CHECK (rate BETWEEN 0 AND 100),
    CONSTRAINT tax_codes_base_chk CHECK (calc_base IN ('net', 'cum')),
    CONSTRAINT tax_codes_order_range CHECK (calc_order BETWEEN 1 AND 9),
    CONSTRAINT tax_codes_applies_chk CHECK (applies_to IN ('both', 'sales', 'purch')),
    -- المعفي: من غير نسبة، نوعه إضافة، وعلى الصافي (بيظهر في الإقرار لوحده — غير الصفري).
    CONSTRAINT tax_codes_exempt_chk CHECK (NOT is_exempt OR (rate = 0 AND kind = 'add' AND calc_base = 'net'))
);
COMMENT ON TABLE acc.tax_codes IS 'أكواد الضريبة — إضافة (بتزوّد الإجمالي) أو خصم (بتقلّله)، وأكتر من ضريبة في الفاتورة';

INSERT INTO acc.tax_codes (code, name_ar, kind, rate, calc_base, calc_order, applies_to, is_exempt) VALUES
    ('VAT14',  'قيمة مضافة 14%',             'add',    14, 'cum', 2, 'both',  false),
    ('VAT0',   'قيمة مضافة صفري (تصدير)',     'add',     0, 'cum', 2, 'sales', false),
    ('EXEMPT', 'معفي من القيمة المضافة',      'add',     0, 'net', 2, 'both',  true),
    ('WHT1',   'خصم من المنبع 1% (توريدات)',  'deduct',  1, 'net', 3, 'both',  false),
    ('WHT3',   'خصم من المنبع 3% (خدمات)',    'deduct',  3, 'net', 3, 'both',  false),
    ('WHT5',   'خصم من المنبع 5% (مهن حرة)',  'deduct',  5, 'net', 3, 'both',  false);

-- الكود ثابت بعد الإضافة (الفواتير هتتربط بيه).
CREATE FUNCTION acc.tax_codes_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code <> OLD.code THEN
        RAISE EXCEPTION 'كود الضريبة % مينفعش يتغيّر بعد الإضافة.', OLD.code;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER tax_codes_guard_trg
    BEFORE UPDATE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION acc.tax_codes_guard();
