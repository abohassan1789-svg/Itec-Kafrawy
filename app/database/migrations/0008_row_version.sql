-- 0008: حماية التعديل في نفس الوقت (Optimistic Locking) — المرحلة 1ج.
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرار المستخدم (2026-11-08): البرنامج على شبكة داخلية 10–20 جهاز، و2–3 ممكن يفتحوا نفس الشاشة.
-- قبل كده «آخر واحد يحفظ بيكسب» (جهاز ممكن يمسح تعديل جهاز تاني من غير ما يعرف). دلوقتي:
--   * كل جدول بيتعدّل من شاشة ليه عمود ``row_version`` (بيبدأ 1).
--   * الشاشة بتحفظ الإصدار اللي فتحت بيه السجل، والحفظ / المسح بشرط ``AND row_version = <الإصدار>``.
--     لو جهاز تاني حفظ قبلك ← مفيش صف بيتأثر ← رسالة «السجل ده اتعدّل من جهاز تاني …» بدل ما يمسح شغله.
--   * الـ trigger هنا بيزوّد الإصدار **بواحد بالظبط** مع أي UPDATE غيّر بيانات حقيقية
--     (أو طلب الزيادة صراحةً بـ ``row_version = row_version + 1``) — ومحدش يقدر يحط رقم بإيده.
--   * الأعمدة «الإدارية» اللي بتتغيّر من غير ما حد يعدّل السجل (آخر دخول، إصدار الصلاحيات)
--     مابتزوّدش الإصدار — عشان دخول المستخدم من جهاز تاني مايعملش «تعارض» على شاشة المستخدمين.
--
-- صلاحيات المجموعة / المستخدم بتتحفظ كمصفوفة واحدة، فالإصدار على «صاحبها» مش على كل سطر:
--   * مجموعات الصلاحيات: بيانات المجموعة + صلاحياتها بيتحفظوا مع بعض ← ``sec.roles.row_version``.
--   * صلاحيات المستخدم (التخصيصات وكلمات سر الإجراءات) ← ``sec.app_users.permissions_version``
--     (عمود لوحده، عشان تعديل بيانات المستخدم وتعديل صلاحياته مايعطّلوش بعض).

-- ---------------------------------------------------------------------------
-- الدالة المشتركة: الإصدار = القديم + 1 لو فيه تغيير حقيقي.
-- البارامترات (TG_ARGV) = أعمدة زيادة مابتحسبش تغيير (غير row_version و updated_at).
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.bump_row_version() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    ignored text[] := ARRAY['row_version', 'updated_at'] || TG_ARGV;
BEGIN
    IF NEW.row_version IS DISTINCT FROM OLD.row_version
       OR (to_jsonb(NEW) - ignored) IS DISTINCT FROM (to_jsonb(OLD) - ignored) THEN
        NEW.row_version := OLD.row_version + 1;
    END IF;
    RETURN NEW;
END $$;
COMMENT ON FUNCTION core.bump_row_version() IS
    'Optimistic Locking: بيزوّد row_version بواحد مع أي تعديل حقيقي (أو طلب صريح) — البارامترات = أعمدة مابتحسبش';

-- ---------------------------------------------------------------------------
-- الأعمدة + الـ Triggers
-- (اسم الـ trigger بيبدأ بـ zz_ عشان يشتغل بعد باقي الـ BEFORE triggers — بيترتبوا بالاسم.)
-- ---------------------------------------------------------------------------
ALTER TABLE sec.roles
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT roles_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON sec.roles
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

ALTER TABLE sec.app_users
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT app_users_row_version_chk CHECK (row_version > 0),
    ADD COLUMN permissions_version integer NOT NULL DEFAULT 1
        CONSTRAINT app_users_permissions_version_chk CHECK (permissions_version > 0);
COMMENT ON COLUMN sec.app_users.permissions_version IS
    'إصدار صلاحيات المستخدم (التخصيصات وكلمات سر الإجراءات) — بيزيد مع كل حفظ من شاشة «صلاحيات المستخدم»';
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON sec.app_users
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version('last_login_at', 'permissions_version');

ALTER TABLE core.company_settings
    ADD COLUMN row_version integer NOT NULL DEFAULT 1
        CONSTRAINT company_settings_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON core.company_settings
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

ALTER TABLE core.fiscal_years
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT fiscal_years_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

ALTER TABLE core.currencies
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT currencies_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON core.currencies
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

ALTER TABLE core.exchange_rates
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT exchange_rates_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON core.exchange_rates
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

ALTER TABLE acc.tax_codes
    ADD COLUMN row_version integer NOT NULL DEFAULT 1 CONSTRAINT tax_codes_row_version_chk CHECK (row_version > 0);
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.tax_codes
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
