-- 0010: ربط أكواد الضريبة بحساباتها في الدليل (المرحلة 2 — بعد «شجرة الحسابات»).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- كل كود ضريبة ليه حسابين في الدليل:
--   * حساب المبيعات: الضريبة اللي على فواتير البيع (ض.ق.م مخرجات 2103 · خصم المنبع اللي العميل خصمه مننا 110602).
--   * حساب المشتريات: الضريبة اللي على فواتير الشراء (ض.ق.م مدخلات 110601 · خصم المنبع اللي خصمناه من المورد 2104).
-- الحساب لازم يبقى **فرعي نشط في الميزانية** (أصل أو خصم) — والحساب المربوط بكود ضريبة مايتمسحش
-- ولا يتوقف ولا يتحوّل رئيسي (غيّر الربط الأول). الفواتير هتاخد الحساب من هنا (من غير بحث بالاسم).

ALTER TABLE acc.tax_codes
    ADD COLUMN sales_account_id    integer REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    ADD COLUMN purchase_account_id integer REFERENCES acc.accounts (id) ON DELETE RESTRICT;
CREATE INDEX tax_codes_sales_account_idx ON acc.tax_codes (sales_account_id);
CREATE INDEX tax_codes_purchase_account_idx ON acc.tax_codes (purchase_account_id);
COMMENT ON COLUMN acc.tax_codes.sales_account_id IS 'حساب الضريبة في فواتير البيع';
COMMENT ON COLUMN acc.tax_codes.purchase_account_id IS 'حساب الضريبة في فواتير الشراء';

-- الحساب المربوط لازم يبقى فرعي نشط في الميزانية.
CREATE FUNCTION acc.tax_codes_accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a     acc.accounts%ROWTYPE;
    acc_id integer;
BEGIN
    FOREACH acc_id IN ARRAY ARRAY[NEW.sales_account_id, NEW.purchase_account_id] LOOP
        CONTINUE WHEN acc_id IS NULL;
        CONTINUE WHEN TG_OP = 'UPDATE' AND acc_id IN (OLD.sales_account_id, OLD.purchase_account_id);
        SELECT * INTO a FROM acc.accounts WHERE id = acc_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'حساب الضريبة مش موجود في الدليل (ممكن يكون اتمسح).';
        END IF;
        IF NOT a.is_leaf OR NOT a.is_active THEN
            RAISE EXCEPTION 'حساب الضريبة «% %» لازم يبقى فرعي ونشط (بيقبل قيود).', a.code, a.name_ar;
        END IF;
        IF a.statement <> 'bs' THEN
            RAISE EXCEPTION 'حساب الضريبة «% %» لازم يبقى من حسابات الميزانية (أصول أو خصوم).', a.code, a.name_ar;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER tax_codes_accounts_trg
    BEFORE INSERT OR UPDATE OF sales_account_id, purchase_account_id ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION acc.tax_codes_accounts_guard();

-- الحساب المربوط بكود ضريبة: مايتمسحش ولا يتوقف ولا يتحوّل رئيسي (رسالة عربي بدل خطأ الـ FK).
CREATE FUNCTION acc.accounts_tax_links_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg(code, '، ' ORDER BY code) INTO linked
    FROM acc.tax_codes WHERE OLD.id IN (sales_account_id, purchase_account_id);
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بأكواد الضريبة (%) — غيّر الربط الأول.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF (OLD.is_active AND NOT NEW.is_active) OR (OLD.is_leaf AND NOT NEW.is_leaf) THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بأكواد الضريبة (%) — مينفعش يتوقف ولا يتحوّل رئيسي.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_tax_links_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_tax_links_guard();

-- الربط الجاهز (بالدور — من غير بحث بالاسم ولا بالكود). ده تجهيز أول مرة مش تعديل من حد،
-- فالإصدار (row_version) بيفضل 1.
ALTER TABLE acc.tax_codes DISABLE TRIGGER zz_row_version_trg;
UPDATE acc.tax_codes t SET
    sales_account_id = CASE
        WHEN t.applies_to = 'purch' OR t.is_exempt THEN NULL
        WHEN t.kind = 'add' THEN (SELECT id FROM acc.accounts WHERE role_code = 'vat_out' AND is_leaf ORDER BY code LIMIT 1)
        ELSE (SELECT id FROM acc.accounts WHERE role_code = 'wht_rec' AND is_leaf ORDER BY code LIMIT 1) END,
    purchase_account_id = CASE
        WHEN t.applies_to = 'sales' OR t.is_exempt THEN NULL
        WHEN t.kind = 'add' THEN (SELECT id FROM acc.accounts WHERE role_code = 'vat_in' AND is_leaf ORDER BY code LIMIT 1)
        ELSE (SELECT id FROM acc.accounts WHERE role_code = 'wht_pay' AND is_leaf ORDER BY code LIMIT 1) END;
ALTER TABLE acc.tax_codes ENABLE TRIGGER zz_row_version_trg;
