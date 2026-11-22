-- 0042: حساب لكل شريك تحت «32 جاري الشركاء» (طلب المستخدم 2026-11-22: «لما أكوّد شريك مابيروحش لدليل الحسابات
-- تحت جاري الشركاء» ← اختار «حساب تحت 32 بس»).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرار:
--   * **كل شريك = حساب فرعي لوحده تحت 32** (3201، 3202 …) باسم الشريك — **بيتعمل لوحده مع إضافة الشريك**
--     (trigger في القاعدة، فأي إضافة من أي مكان بتعمل حسابها) ويتمسح معاه. اسم الحساب بيتغيّر مع اسم الشريك لو كان زيه.
--   * **رأس المال فاضل على «31 رأس المال» واحد ببُعد الشريك** (المستخدم اختار 32 بس).
--   * 32 بيتحوّل **رئيسي** بالتفريع العادي (acc.split_account) وتحته «3299 جاري الشركاء — عام» — عليه مفتاح
--     `partner_cur` في الحسابات الافتراضية (احتياطي بس) — والقيود القديمة اللي عليها شريك بتتنقل لحساب الشريك نفسه.
--   * حساب الشريك: **بُعد الشريك فاضل على السطر** (نفس الكشف والأرصدة)، والشريك على السطر **لازم يبقى صاحب الحساب**.
--     مايتمسحش لوحده ولا يتفرّع ولا يتنقل ولا يتغيّر دوره، ومايتوقفش والشريك نشط (اللي خرج بيسحب رصيده منه).
--   * محرك الترحيل: قاعدة «طرف شريك + مفتاح partner_cur» ← حساب الشريك نفسه (posting_service).

-- ---------------------------------------------------------------------------
-- 1) العمود + دالة عمل الحساب
-- ---------------------------------------------------------------------------
ALTER TABLE party.partners ADD COLUMN account_id integer REFERENCES acc.accounts (id) ON DELETE RESTRICT;
ALTER TABLE party.partners ADD CONSTRAINT partners_account_unique UNIQUE (account_id);
COMMENT ON COLUMN party.partners.account_id IS 'حساب جاري الشريك (فرعي تحت 32 — بيتعمل لوحده مع الشريك)';

-- الحساب الجاي تحت أعلى حساب رئيسي نشط بدور «جاري شريك» (32): نفس قاعدة الشاشة (أكبر كود + 1، ولو خلصوا أول فاضي).
-- FOR UPDATE على الأب: جهازين بيضيفوا شريك في نفس اللحظة مياخدوش نفس الكود.
CREATE FUNCTION party.partner_new_account(p_name_ar text, p_name_en text) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    p      acc.accounts%ROWTYPE;
    w      integer;
    lim    integer;
    used   integer[];
    n      integer;
    v_id   integer;
BEGIN
    SELECT * INTO p FROM acc.accounts
     WHERE role_code = 'partner' AND NOT is_leaf AND is_active
     ORDER BY level, code LIMIT 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش حساب رئيسي نشط بدور «جاري شريك» (32 جاري الشركاء) — الشريك محتاج يتعمل له حساب تحته.';
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'الحساب «% %» في آخر مستوى — مينفعش يتعمل تحته حساب للشريك.', p.code, p.name_ar;
    END IF;
    w := CASE WHEN p.level <= 1 THEN 1 ELSE 2 END;
    lim := (10 ^ w)::integer - 1;
    SELECT array_agg(substr(code, length(p.code) + 1)::integer) INTO used
      FROM acc.accounts
     WHERE left(code, length(p.code)) = p.code AND length(code) = length(p.code) + w
       AND substr(code, length(p.code) + 1) ~ '^[0-9]+$';
    n := coalesce((SELECT max(x) FROM unnest(used) x), 0) + 1;
    IF n > lim THEN
        SELECT min(g) INTO n FROM generate_series(1, lim) g WHERE g <> ALL (coalesce(used, '{}'));
        IF n IS NULL THEN
            RAISE EXCEPTION 'مفيش أكواد فاضية تحت «% %» لحساب الشريك.', p.code, p.name_ar;
        END IF;
    END IF;
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, party_kind, requires_cost_center, currency_code,
                              is_active, is_system)
    VALUES (p.code || lpad(n::text, w, '0'), left(btrim(p_name_ar), 120), left(nullif(btrim(p_name_en), ''), 120),
            p.id, true, p.account_type, p.nature, p.classification, p.cash_flow, p.role_code, p.party_kind,
            p.requires_cost_center, p.currency_code, true, false)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------
-- 2) 32 يتحوّل رئيسي (التفريع العادي — «— عام» بكود 3299 عشان الشركاء ياخدوا من 3201) + حساب لكل شريك موجود
--    + السطور اللي عليها شريك (القيود والأرصدة الافتتاحية — حتى المرحّلة) بتتنقل لحساب الشريك.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    a          acc.accounts%ROWTYPE;
    v_general  integer;
    r          record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM acc.accounts WHERE role_code = 'partner' AND NOT is_leaf AND is_active) THEN
        SELECT * INTO a FROM acc.accounts
         WHERE id = (SELECT account_id FROM acc.default_accounts WHERE key = 'partner_cur');
        IF NOT FOUND THEN
            SELECT * INTO a FROM acc.accounts WHERE role_code = 'partner' AND is_leaf AND is_active
             ORDER BY level, code LIMIT 1;
        END IF;
        IF FOUND THEN
            -- (المفتاح بيتنقل للعام من غير ما إصداره يزيد — زي 0041)
            ALTER TABLE acc.default_accounts DISABLE TRIGGER zz_row_version_trg;
            v_general := acc.split_account(a.id, a.code || CASE WHEN a.level <= 1 THEN '9' ELSE '99' END);
            ALTER TABLE acc.default_accounts ENABLE TRIGGER zz_row_version_trg;
        END IF;
    END IF;

    ALTER TABLE party.partners DISABLE TRIGGER zz_row_version_trg;
    FOR r IN SELECT id, name_ar, name_en FROM party.partners WHERE account_id IS NULL ORDER BY seq LOOP
        UPDATE party.partners SET account_id = party.partner_new_account(r.name_ar, r.name_en) WHERE id = r.id;
    END LOOP;
    ALTER TABLE party.partners ENABLE TRIGGER zz_row_version_trg;

    IF v_general IS NOT NULL THEN
        PERFORM set_config('acc.journal_line_move', 'on', true);
        UPDATE acc.journal_lines l SET account_id = p.account_id
          FROM party.partners p WHERE l.partner_id = p.id AND l.account_id = v_general;
        PERFORM set_config('acc.journal_line_move', '', true);
        PERFORM set_config('acc.opening_line_move', 'on', true);
        UPDATE acc.opening_balance_lines l SET account_id = p.account_id
          FROM party.partners p WHERE l.partner_id = p.id AND l.account_id = v_general;
        PERFORM set_config('acc.opening_line_move', '', true);
    END IF;
END $$;

ALTER TABLE party.partners ALTER COLUMN account_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 3) الشريك: الحساب بيتعمل لوحده مع الإضافة وثابت بعدها · الاسم بيتغيّر معاه · بيتمسح معاه
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.partners_account_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.account_id IS NOT NULL THEN
            RAISE EXCEPTION 'حساب الشريك بيتعمل لوحده تحت «جاري الشركاء» — مايتختارش.';
        END IF;
        NEW.account_id := party.partner_new_account(NEW.name_ar, NEW.name_en);
    ELSIF NEW.account_id IS DISTINCT FROM OLD.account_id THEN
        RAISE EXCEPTION 'حساب الشريك «%» ثابت — مينفعش يتغيّر.', OLD.code;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER partners_account_trg
    BEFORE INSERT OR UPDATE ON party.partners
    FOR EACH ROW EXECUTE FUNCTION party.partners_account_guard();

CREATE FUNCTION party.partners_sync_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM acc.accounts WHERE id = OLD.account_id;
        RETURN NULL;
    END IF;
    IF NEW.name_ar IS DISTINCT FROM OLD.name_ar OR NEW.name_en IS DISTINCT FROM OLD.name_en THEN
        UPDATE acc.accounts
           SET name_ar = CASE WHEN name_ar = left(btrim(OLD.name_ar), 120) THEN left(btrim(NEW.name_ar), 120)
                              ELSE name_ar END,
               name_en = CASE WHEN name_en IS NOT DISTINCT FROM left(nullif(btrim(OLD.name_en), ''), 120)
                              THEN left(nullif(btrim(NEW.name_en), ''), 120) ELSE name_en END
         WHERE id = NEW.account_id
           AND (name_ar = left(btrim(OLD.name_ar), 120)
                OR name_en IS NOT DISTINCT FROM left(nullif(btrim(OLD.name_en), ''), 120));
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER partners_sync_account_trg
    AFTER UPDATE OR DELETE ON party.partners
    FOR EACH ROW EXECUTE FUNCTION party.partners_sync_account();

-- ---------------------------------------------------------------------------
-- 4) حماية حساب الشريك من شجرة الحسابات
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_partner_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pr  party.partners%ROWTYPE;
BEGIN
    SELECT * INTO pr FROM party.partners WHERE account_id = OLD.id;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» حساب الشريك «% %» — امسح الشريك نفسه (الحساب بيتمسح معاه).',
            OLD.code, OLD.name_ar, pr.code, pr.name_ar;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» حساب الشريك «% %» — مينفعش يتحوّل رئيسي ولا يتفرّع.',
            OLD.code, OLD.name_ar, pr.code, pr.name_ar;
    END IF;
    IF NEW.parent_id IS DISTINCT FROM OLD.parent_id OR NEW.role_code IS DISTINCT FROM OLD.role_code
       OR NEW.party_kind IS DISTINCT FROM OLD.party_kind THEN
        RAISE EXCEPTION 'الحساب «% %» حساب الشريك «% %» — مكانه ودوره وطرفه ثابتين.',
            OLD.code, OLD.name_ar, pr.code, pr.name_ar;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active AND pr.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» حساب الشريك «% %» وهو نشط — وقّف الشريك الأول.',
            OLD.code, OLD.name_ar, pr.code, pr.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zpartner_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_partner_guard();

-- ---------------------------------------------------------------------------
-- 5) السطر على حساب شريك ← الشريك على السطر لازم يبقى صاحبه (القيود والأرصدة الافتتاحية)
--    (الاسم بـ z عشان رسايل «القيد مرحّل» و«لازم تختار الشريك» تطلع الأول.)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.partner_account_line_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pr  party.partners%ROWTYPE;
    a   acc.accounts%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.account_id = OLD.account_id
       AND NEW.partner_id IS NOT DISTINCT FROM OLD.partner_id THEN
        RETURN NEW;
    END IF;
    SELECT * INTO pr FROM party.partners WHERE account_id = NEW.account_id;
    IF FOUND AND NEW.partner_id IS DISTINCT FROM pr.id THEN
        SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id;
        RAISE EXCEPTION 'الحساب «% %» حساب الشريك «% %» — الشريك على السطر لازم يبقى هو.',
            a.code, a.name_ar, pr.code, pr.name_ar;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER journal_lines_zpartner_trg
    BEFORE INSERT OR UPDATE ON acc.journal_lines
    FOR EACH ROW EXECUTE FUNCTION acc.partner_account_line_check();
CREATE TRIGGER opening_lines_zpartner_trg
    BEFORE INSERT OR UPDATE ON acc.opening_balance_lines
    FOR EACH ROW EXECUTE FUNCTION acc.partner_account_line_check();

COMMENT ON TABLE party.partners IS
    'الشركاء: كل شريك حساب فرعي تحت 32 جاري الشركاء (بيتعمل لوحده) + بُعد على 31 رأس المال · الكود PRT-001 · '
    'نسبة الربح والخسارة (الموقوف = 0)';
