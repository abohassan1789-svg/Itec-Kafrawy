-- 0022: الشركاء (المرحلة 3ب — شاشة «الشركاء»، النموذج 1 اختيار المستخدم 2026-11-14).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/06_partners.html — المستخدم اختار النموذج 1 من غير رد على الأسئلة ← بالاقتراحات):
--   * الشاشة لتعريف الشريك بس — السحب/الإيداع وزيادة رأس المال وتوزيع الأرباح مستندات في المرحلة 5.
--   * **الكود** `PRT-001`, `PRT-002`… بيتولّد لوحده وثابت (زي الموردين والموظفين): المسلسل في `seq` والكود محسوب منه.
--   * **نسبة الربح** و**نسبة الخسارة** (الشاشة فيها «الخسارة بنفس نسبة الربح» — الافتراضي) من 0 لـ 100.
--   * **مجموع نسب الشركاء النشطين ≠ 100%** ← تنبيه بس في الشاشة (عشان الشركاء بيتضافوا واحد واحد)، والمنع وقت
--     توزيع الأرباح (المرحلة 5).
--   * **الشريك اللي خرج (موقوف) نسبته لازم تبقى 0** — عشان مايدخلش في توزيع الأرباح.
--   * **رأس المال لكل شريك:** «رأس المال المكتتب» رقم مرجعي، والمدفوع الفعلي من القيود على «31 رأس المال»
--     **ببُعد الشريك** (السكريبت ده بيخلّي حسابات دور «رأس المال» تطلب طرف «شريك» زي «32 جاري الشركاء»).
--   * **نوع الشريك:** متضامن (general) / موصي (limited).
--   * الشاشة تحت «الحسابات» ← «حقوق الملكية» ← «الشركاء».
-- لسه: الشريك اللي عليه قيد مايتمسحش (FK RESTRICT من سطور القيود — المرحلة 4).

CREATE TABLE party.partners (
    id                  integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq                 integer       NOT NULL,
    code                varchar(20)   GENERATED ALWAYS AS
                            ('PRT-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar             varchar(200)  NOT NULL,
    name_en             varchar(200),
    partner_type        varchar(10)   NOT NULL DEFAULT 'general',      -- general متضامن · limited موصي
    mobile              varchar(30),
    phone               varchar(30),
    email               varchar(120),
    address             varchar(300),
    national_id         varchar(14),                                   -- اختياري: 14 رقم ومايتكررش
    join_date           date,                                          -- تاريخ الدخول (اختياري)
    profit_percent      numeric(5,2)  NOT NULL DEFAULT 0,
    loss_percent        numeric(5,2)  NOT NULL DEFAULT 0,
    subscribed_capital  numeric(18,2) NOT NULL DEFAULT 0,              -- رأس المال المكتتب (مرجعي)
    notes               varchar(500),
    is_active           boolean       NOT NULL DEFAULT true,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    row_version         integer       NOT NULL DEFAULT 1 CONSTRAINT partners_row_version_chk CHECK (row_version > 0),
    CONSTRAINT partners_seq_unique UNIQUE (seq),
    CONSTRAINT partners_seq_range CHECK (seq > 0),
    CONSTRAINT partners_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT partners_type_chk CHECK (partner_type IN ('general', 'limited')),
    CONSTRAINT partners_nid_format CHECK (national_id IS NULL OR national_id ~ '^[0-9]{14}$'),
    CONSTRAINT partners_join_range CHECK (join_date IS NULL OR join_date BETWEEN DATE '1950-01-01' AND DATE '2100-12-31'),
    CONSTRAINT partners_profit_range CHECK (profit_percent BETWEEN 0 AND 100),
    CONSTRAINT partners_loss_range CHECK (loss_percent BETWEEN 0 AND 100),
    CONSTRAINT partners_capital_chk CHECK (subscribed_capital >= 0),
    -- الشريك اللي خرج مايدخلش في توزيع الأرباح
    CONSTRAINT partners_inactive_zero CHECK (is_active OR (profit_percent = 0 AND loss_percent = 0))
);
CREATE UNIQUE INDEX partners_code_unique ON party.partners (code);
CREATE UNIQUE INDEX partners_nid_unique ON party.partners (national_id) WHERE national_id IS NOT NULL;
CREATE UNIQUE INDEX partners_name_unique ON party.partners (lower(btrim(name_ar)));
CREATE INDEX partners_mobile_idx ON party.partners (mobile);
CREATE INDEX partners_active_idx ON party.partners (is_active);
COMMENT ON TABLE party.partners IS
    'الشركاء: بُعد على 32 جاري الشركاء و31 رأس المال · الكود PRT-001 · نسبة الربح والخسارة (الموقوف = 0)';

-- ---------------------------------------------------------------------------
-- الحماية (تعديل): الكود ثابت.
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.partners_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.seq <> OLD.seq THEN
        RAISE EXCEPTION 'كود الشريك «%» ثابت — مينفعش يتغيّر.', OLD.code;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER partners_guard_trg
    BEFORE UPDATE ON party.partners
    FOR EACH ROW EXECUTE FUNCTION party.partners_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON party.partners
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- رأس المال ببُعد الشريك (زي جاري الشركاء): الدور بيقترح الطرف للحسابات الجديدة، والحسابات الموجودة بتطلبه.
-- ---------------------------------------------------------------------------
UPDATE acc.account_roles SET default_party_kind = 'partner' WHERE code = 'capital';
UPDATE acc.accounts SET party_kind = 'partner' WHERE role_code = 'capital' AND party_kind IS NULL;
