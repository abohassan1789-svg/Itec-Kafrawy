-- 0023: الخزائن (المرحلة 3ج — شاشة «الخزائن»، النموذج 1 اختيار المستخدم 2026-11-15).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/07_treasuries.html — المستخدم اختار النموذج 1 + الاقتراحات):
--   * الشاشة لتعريف الخزنة بس — الفلوس اللي بتدخل وتخرج (سندات / حركات الخزائن والبنوك) في المرحلة 5،
--     والرصيد بيتحسب من القيود على حساب الخزنة (لسه).
--   * **الكود** `TRS-001`, `TRS-002`… بيتولّد لوحده وثابت (زي SUB / EMP / PRT): المسلسل في `seq`.
--   * **كل خزنة = حساب فرعي لوحدها** بدور «خزينة» (cash) — **بيتعمل لوحده** تحت «1101 النقدية بالخزائن» باسم
--     الخزنة وعملتها، أو **بيتختار حساب موجود** فرعي بدور «خزينة» ومش مربوط بخزنة تانية. الحساب ثابت بعد الحفظ،
--     ومايتربطش بخزنتين (UNIQUE).
--   * **عملة واحدة** لكل خزنة = عملة حسابها. تغيير عملة الخزنة بيغيّر عملة حسابها معاها (نفس الـ transaction).
--     TODO (المرحلة 5): العملة ماتتغيّرش بعد أول حركة.
--   * اسم الحساب بيتغيّر مع اسم الخزنة **لو كان زيه** (الحساب اللي اتعمل لوحده أو اللي اسمه زي الخزنة).
--   * **أمين الخزينة** اختياري — موظف نشط عليه دور «أمين خزينة»، والموظف يقدر يمسك أكتر من خزنة.
--   * **حد أقصى للرصيد** اختياري — تنبيه بس (مع السندات في المرحلة 5).
--   * **الخزينة الافتراضية** = الخزنة اللي حسابها هو «الخزينة الافتراضية» (cash_box) في شاشة الحسابات الافتراضية
--     (مكان واحد بس للاختيار) — ماتتوقفش ولا تتمسح.
--   * **رصيد أول المدة** من «مستند الأرصدة الافتتاحية» (المرحلة 4) — الشاشة بتعرضه بس.
--   * كل الخزائن لكل المستخدمين (زي قرار المخازن).
--   * الخزنتين الأولانيين: «الخزينة الرئيسية» على 110101 (الافتراضية) و«خزينة النثريات» على 110102.
-- لسه: الخزنة اللي عليها حركة ماتتمسحش (FK RESTRICT من السندات — المرحلة 5).

CREATE TABLE cash.treasuries (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq             integer       NOT NULL,
    code            varchar(20)   GENERATED ALWAYS AS
                        ('TRS-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar         varchar(120)  NOT NULL,                             -- 120 = طول اسم الحساب
    name_en         varchar(120),
    currency_code   char(3)       NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    account_id      integer       NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    owns_account    boolean       NOT NULL DEFAULT false,               -- الحساب اتعمل مع الخزنة (بيتمسح معاها)
    treasurer_id    integer       REFERENCES party.employees (id) ON DELETE RESTRICT,
    location        varchar(200),
    max_balance     numeric(18,2),                                      -- اختياري: تنبيه لو الرصيد عدّاه
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT treasuries_row_version_chk CHECK (row_version > 0),
    CONSTRAINT treasuries_seq_unique UNIQUE (seq),
    CONSTRAINT treasuries_seq_range CHECK (seq > 0),
    CONSTRAINT treasuries_account_unique UNIQUE (account_id),
    CONSTRAINT treasuries_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT treasuries_max_balance_chk CHECK (max_balance IS NULL OR max_balance > 0)
);
CREATE UNIQUE INDEX treasuries_code_unique ON cash.treasuries (code);
CREATE UNIQUE INDEX treasuries_name_unique ON cash.treasuries (lower(btrim(name_ar)));
CREATE INDEX treasuries_currency_idx ON cash.treasuries (currency_code);
CREATE INDEX treasuries_treasurer_idx ON cash.treasuries (treasurer_id);
CREATE INDEX treasuries_active_idx ON cash.treasuries (is_active);
COMMENT ON TABLE cash.treasuries IS
    'الخزائن: كل خزنة حساب فرعي بدور «خزينة» (تحت 1101) + عملة + أمين (موظف) · الكود TRS-001 · الرصيد من القيود';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.treasuries_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a  acc.accounts%ROWTYPE;
    e  party.employees%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM acc.default_accounts WHERE key = 'cash_box' AND account_id = OLD.account_id) THEN
            RAISE EXCEPTION 'الخزنة «% %» هي الخزينة الافتراضية — غيّرها من شاشة «الحسابات الافتراضية» الأول.',
                OLD.code, OLD.name_ar;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.seq <> OLD.seq THEN
            RAISE EXCEPTION 'كود الخزنة «%» ثابت — مينفعش يتغيّر.', OLD.code;
        END IF;
        IF NEW.account_id <> OLD.account_id OR NEW.owns_account <> OLD.owns_account THEN
            RAISE EXCEPTION 'حساب الخزنة «%» ثابت بعد الحفظ — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- الحساب: فرعي بدور «خزينة»، ونشط لو الخزنة نشطة، وبنفس العملة (في الإضافة — التعديل بيغيّر عملة الحساب معاه).
    SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'حساب الخزنة مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT a.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» رئيسي — الخزنة لازم يبقى حسابها فرعي.', a.code, a.name_ar;
        END IF;
        IF a.role_code IS DISTINCT FROM 'cash' THEN
            RAISE EXCEPTION 'الحساب «% %» مش بدور «خزينة» — اختار حساب تحت «النقدية بالخزائن».', a.code, a.name_ar;
        END IF;
        IF a.currency_code <> NEW.currency_code THEN
            RAISE EXCEPTION 'عملة الحساب «% %» (%) غير عملة الخزنة (%).', a.code, a.name_ar, a.currency_code,
                NEW.currency_code;
        END IF;
    END IF;
    IF NEW.is_active AND NOT a.is_active
       AND (TG_OP = 'INSERT' OR NOT OLD.is_active) THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه من شجرة الحسابات الأول.', a.code, a.name_ar;
    END IF;

    -- الخزينة الافتراضية ماتتوقفش.
    IF NOT NEW.is_active AND (TG_OP = 'INSERT' OR OLD.is_active)
       AND EXISTS (SELECT 1 FROM acc.default_accounts WHERE key = 'cash_box' AND account_id = NEW.account_id) THEN
        RAISE EXCEPTION 'الخزنة «%» هي الخزينة الافتراضية — مينفعش تتوقف (غيّرها من شاشة «الحسابات الافتراضية» الأول).',
            NEW.name_ar;
    END IF;

    -- الأمين: موظف نشط عليه دور «أمين خزينة» (لما يتحدد أو يتغيّر أو الخزنة تتنشّط).
    IF NEW.treasurer_id IS NOT NULL AND NEW.is_active
       AND (TG_OP = 'INSERT' OR NEW.treasurer_id IS DISTINCT FROM OLD.treasurer_id OR NOT OLD.is_active) THEN
        SELECT * INTO e FROM party.employees WHERE id = NEW.treasurer_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'أمين الخزينة مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NOT e.is_treasurer THEN
            RAISE EXCEPTION 'الموظف «% %» مش عليه دور «أمين خزينة» (فعّله من شاشة الموظفين).', e.code, e.name_ar;
        END IF;
        IF NOT e.is_active THEN
            RAISE EXCEPTION 'الموظف «% %» موقوف — مينفعش يبقى أمين خزينة.', e.code, e.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER treasuries_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON cash.treasuries
    FOR EACH ROW EXECUTE FUNCTION cash.treasuries_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.treasuries
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- بعد التعديل: عملة الحساب (دايماً) واسمه (لو كان زي اسم الخزنة القديم) بيتغيّروا مع الخزنة.
CREATE FUNCTION cash.treasuries_sync_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.currency_code IS DISTINCT FROM OLD.currency_code OR NEW.name_ar IS DISTINCT FROM OLD.name_ar THEN
        PERFORM set_config('cash.sync_account_id', NEW.account_id::text, true);
        UPDATE acc.accounts
           SET currency_code = NEW.currency_code,
               name_ar = CASE WHEN name_ar = OLD.name_ar THEN NEW.name_ar ELSE name_ar END
         WHERE id = NEW.account_id
           AND (currency_code <> NEW.currency_code OR (name_ar = OLD.name_ar AND OLD.name_ar <> NEW.name_ar));
        PERFORM set_config('cash.sync_account_id', '', true);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER treasuries_sync_account_trg
    AFTER UPDATE ON cash.treasuries
    FOR EACH ROW EXECUTE FUNCTION cash.treasuries_sync_account();

-- ---------------------------------------------------------------------------
-- حماية حساب الخزنة من شجرة الحسابات: مايتمسحش ولا يتحوّل رئيسي (ولا يتفرّع) ولا يغيّر دوره،
-- ومايتوقفش والخزنة نشطة، وعملته من شاشة الخزائن بس. (الاسم accounts_z… عشان رسايل النظامي تطلع الأول.)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_treasury_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  cash.treasuries%ROWTYPE;
BEGIN
    SELECT * INTO t FROM cash.treasuries WHERE account_id = OLD.id;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالخزنة «% %» — امسح الخزنة الأول.', OLD.code, OLD.name_ar, t.code, t.name_ar;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالخزنة «% %» — مينفعش يتحوّل رئيسي ولا يتفرّع.',
            OLD.code, OLD.name_ar, t.code, t.name_ar;
    END IF;
    IF NEW.role_code IS DISTINCT FROM OLD.role_code THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالخزنة «% %» — دوره «خزينة» ثابت.', OLD.code, OLD.name_ar, t.code, t.name_ar;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active AND t.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالخزنة «% %» وهي نشطة — وقّف الخزنة الأول.',
            OLD.code, OLD.name_ar, t.code, t.name_ar;
    END IF;
    IF NEW.currency_code <> OLD.currency_code
       AND coalesce(current_setting('cash.sync_account_id', true), '') <> OLD.id::text THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالخزنة «% %» — عملته بتتغيّر من شاشة الخزائن بس.',
            OLD.code, OLD.name_ar, t.code, t.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_ztreasury_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_treasury_guard();

-- ---------------------------------------------------------------------------
-- «الخزينة الافتراضية» (cash_box) في شاشة الحسابات الافتراضية لازم تبقى حساب خزنة نشطة
-- (FOR SHARE على الخزنة: لو جهاز تاني بيوقفها في نفس اللحظة، واحد يستنى التاني).
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.default_accounts_treasury_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  cash.treasuries%ROWTYPE;
BEGIN
    IF NEW.key = 'cash_box' AND NEW.account_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
        SELECT * INTO t FROM cash.treasuries WHERE account_id = NEW.account_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '«الخزينة الافتراضية» لازم تبقى حساب خزنة — الحساب ده مش مربوط بخزنة (اعمله خزنة من شاشة الخزائن الأول).';
        END IF;
        IF NOT t.is_active THEN
            RAISE EXCEPTION 'الخزنة «% %» موقوفة — نشّطها الأول عشان تبقى الخزينة الافتراضية.', t.code, t.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER default_accounts_ztreasury_trg
    BEFORE INSERT OR UPDATE ON acc.default_accounts
    FOR EACH ROW EXECUTE FUNCTION acc.default_accounts_treasury_guard();

-- ---------------------------------------------------------------------------
-- حماية أمين الخزينة من شاشة الموظفين: مايتمسحش، ومايتشالش منه دور «أمين خزينة» ولا يتوقف وهو أمين على خزنة نشطة.
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.employees_treasury_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    SELECT string_agg('«' || name_ar || '»', '، ' ORDER BY seq) INTO linked
      FROM cash.treasuries
     WHERE treasurer_id = OLD.id AND (TG_OP = 'DELETE' OR is_active);
    IF linked IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على الخزنة % — غيّر الأمين من شاشة الخزائن الأول.', OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_treasurer AND NOT NEW.is_treasurer THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على الخزنة % — غيّر الأمين من شاشة الخزائن الأول قبل ما تشيل الدور.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active THEN
        RAISE EXCEPTION 'الموظف «% %» أمين على الخزنة % — غيّر الأمين من شاشة الخزائن الأول قبل ما توقفه.',
            OLD.code, OLD.name_ar, linked;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER employees_ztreasury_guard_trg
    BEFORE UPDATE OR DELETE ON party.employees
    FOR EACH ROW EXECUTE FUNCTION party.employees_treasury_guard();

-- ---------------------------------------------------------------------------
-- الخزنتين الأولانيين (لو حساباتهم لسه فرعي نشط بدور «خزينة» في الدليل).
-- ---------------------------------------------------------------------------
INSERT INTO cash.treasuries (seq, name_ar, name_en, currency_code, account_id, location)
SELECT t.seq, t.name_ar, t.name_en, a.currency_code, a.id, t.location
  FROM (VALUES (1, '110101', 'الخزينة الرئيسية', 'Main Cash Box', 'المكتب الرئيسي'),
               (2, '110102', 'خزينة النثريات', 'Petty Cash', 'المكتب الرئيسي')) AS t (seq, code, name_ar, name_en, location)
  JOIN acc.accounts a ON a.code = t.code AND a.is_leaf AND a.is_active AND a.role_code = 'cash'
 ORDER BY t.seq;
