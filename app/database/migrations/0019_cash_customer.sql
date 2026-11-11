-- 0019: «عميل نقدي» جاهز للبيع الكاش (رد المستخدم 2026-11-11 على سؤال العملاء 6).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
--   * عميل **نظامي** كوده 0 (العملاء العاديين بيبدأوا من 1 زي ما المستخدم قرر)، في المجموعة النظامية
--     «عملاء — عام»، مدة السداد 0 (نقدي)، من غير حد ائتمان، من غير رقم ضريبي، بالعملة الأساسية وض.ق.م المجموعة.
--   * **مابيتمسحش ولا بيتوقف** (فاتورة الكاش هتحتاجه دايماً) — اسمه وباقي بياناته بتتعدّل عادي.
--   * «نظامي» بيتحدد من البرنامج بس.

ALTER TABLE party.customers ADD COLUMN is_system boolean NOT NULL DEFAULT false;
ALTER TABLE party.customers
    ADD CONSTRAINT customers_system_active CHECK (is_active OR NOT is_system);

CREATE FUNCTION party.customers_system_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_system THEN
            RAISE EXCEPTION 'العميل «%» نظامي (للبيع الكاش) — مابيتمسحش.', OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.is_system <> OLD.is_system THEN
        RAISE EXCEPTION '«نظامي» بيتحدد من البرنامج بس.';
    END IF;
    IF OLD.is_system AND NOT NEW.is_active THEN
        RAISE EXCEPTION 'العميل «%» نظامي (للبيع الكاش) — مابيتوقفش.', OLD.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER customers_system_guard_trg
    BEFORE UPDATE OR DELETE ON party.customers
    FOR EACH ROW EXECUTE FUNCTION party.customers_system_guard();

-- العميل النقدي (لو المجموعة النظامية موجودة — بتتعمل في 0014).
INSERT INTO party.customers (code, name_ar, name_en, group_id, payment_terms_days, credit_limit, currency_code,
                             vat_tax_code_id, wht_tax_code_id, notes, is_system)
SELECT '0', 'عميل نقدي', 'Cash Customer', g.id, 0, 0,
       coalesce((SELECT code FROM core.currencies WHERE is_base LIMIT 1), g.currency_code),
       g.vat_tax_code_id, NULL, 'للبيع الكاش من غير بيانات عميل', true
FROM party.customer_groups g
WHERE g.is_system
  AND NOT EXISTS (SELECT 1 FROM party.customers WHERE code = '0')
ORDER BY g.code::bigint
LIMIT 1;
