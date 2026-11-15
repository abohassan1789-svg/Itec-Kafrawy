-- 0024: البنوك والحسابات البنكية (المرحلة 3ج — شاشة «البنوك»، النموذج 7 «البنوك في دليل الحسابات»
-- اختيار المستخدم 2026-11-15 + كل الاقتراحات).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/08_banks.html):
--   * **مستويين:** البنك (`cash.banks`) مرة واحدة: الاسم + EN + اسم مختصر + الفرع + العنوان + SWIFT + مسؤول الحساب
--     وتليفونه — وتحته حساب أو أكتر (`cash.bank_accounts`).
--   * **الكود:** البنك `BNK-001` (من `seq`) · الحساب البنكي `BNK-001-01` (كود البنك + مسلسل جوه البنك) — ثابتين.
--   * **كل حساب بنكي = حساب فرعي لوحده** بدور «بنك» (bank) — **بيتعمل لوحده** تحت «1102 البنوك» باسم
--     «<البنك> — <النوع> [العملة لو مش الأساسية]» (زي «البنك الأهلي — جاري»)، أو **بيتختار حساب موجود** فرعي بدور
--     «بنك» ومش مربوط. الحساب ثابت بعد الحفظ ومايتربطش بحسابين بنكيين (UNIQUE).
--   * **النوع:** جاري · توفير · وديعة لأجل (تاريخ استحقاق اختياري) · جاري مدين (تسهيلات — لازم حد تسهيلات).
--   * **الرصيد السالب** ممنوع إلا الجاري المدين لحد التسهيلات (المنع نفسه وقت السند — المرحلة 5).
--   * **عملة واحدة** = عملة الحساب — تغييرها بيغيّر عملة الحساب معاها. TODO (المرحلة 5): ماتتغيّرش بعد أول حركة.
--   * اسم الحساب بيتغيّر مع النوع / العملة / اسم البنك **لو كان زي الاسم المتولّد القديم**.
--   * **رقم الحساب** مطلوب (من الشاشة) ومايتكررش جوه نفس البنك · **IBAN** اختياري ومايتكررش، والمصري EG + 27 رقم.
--   * **البنك الافتراضي** = الحساب البنكي اللي حسابه «البنك الافتراضي» (bank) في شاشة الحسابات الافتراضية —
--     مايتوقفش ولا يتمسح.
--   * **رصيد أول المدة** من «مستند الأرصدة الافتتاحية» (المرحلة 4) — الشاشة بتعرضه بس.
--   * البنك اللي عليه حسابات مايتمسحش، ومايتوقفش وحساب منهم نشط.
--   * البنكين الأولانيين: «البنك الأهلي المصري» بحساب جاري على 110201 (الافتراضي) و«بنك مصر» بحساب جاري على 110202
--     (من غير رقم حساب — بيتكتب أول ما حد يعدّل).
-- لسه: الحساب البنكي اللي عليه حركة مايتمسحش (FK RESTRICT من السندات — المرحلة 5).

CREATE TABLE cash.banks (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq             integer       NOT NULL,
    code            varchar(20)   GENERATED ALWAYS AS
                        ('BNK-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar         varchar(120)  NOT NULL,
    name_en         varchar(120),
    short_name      varchar(60),                                        -- في اسم الحساب: «البنك الأهلي — جاري»
    branch          varchar(120),
    address         varchar(250),
    swift_code      varchar(11),
    contact_name    varchar(120),
    contact_phone   varchar(30),
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT banks_row_version_chk CHECK (row_version > 0),
    CONSTRAINT banks_seq_unique UNIQUE (seq),
    CONSTRAINT banks_seq_range CHECK (seq > 0),
    CONSTRAINT banks_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT banks_swift_format CHECK (swift_code IS NULL OR swift_code ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$')
);
CREATE UNIQUE INDEX banks_code_unique ON cash.banks (code);
CREATE UNIQUE INDEX banks_name_unique ON cash.banks (lower(btrim(name_ar)));
CREATE INDEX banks_active_idx ON cash.banks (is_active);
COMMENT ON TABLE cash.banks IS 'البنوك: الاسم والفرع وSWIFT ومسؤول الحساب · الكود BNK-001 · وتحت كل بنك حساباته (cash.bank_accounts)';

CREATE TABLE cash.bank_accounts (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bank_id         integer       NOT NULL REFERENCES cash.banks (id) ON DELETE RESTRICT,
    seq             integer       NOT NULL,                             -- مسلسل جوه البنك
    code            varchar(20)   NOT NULL,                             -- BNK-001-01 (بيتحسب في الـ trigger)
    account_type    varchar(10)   NOT NULL DEFAULT 'current',
    currency_code   char(3)       NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    account_number  varchar(34),                                        -- مطلوب من الشاشة (البنوك الجاهزة فاضية)
    iban            varchar(34),                                        -- من غير مسافات وكابيتال
    overdraft_limit numeric(18,2),                                      -- الجاري المدين بس
    maturity_date   date,                                               -- الوديعة بس (اختياري)
    account_id      integer       NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    owns_account    boolean       NOT NULL DEFAULT false,               -- الحساب اتعمل معاه (بيتمسح معاه)
    notes           varchar(500),
    is_active       boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT bank_accounts_row_version_chk CHECK (row_version > 0),
    CONSTRAINT bank_accounts_seq_unique UNIQUE (bank_id, seq),
    CONSTRAINT bank_accounts_seq_range CHECK (seq > 0),
    CONSTRAINT bank_accounts_account_unique UNIQUE (account_id),
    CONSTRAINT bank_accounts_type_chk CHECK (account_type IN ('current', 'savings', 'deposit', 'overdraft')),
    CONSTRAINT bank_accounts_number_not_blank CHECK (account_number IS NULL OR btrim(account_number) <> ''),
    CONSTRAINT bank_accounts_iban_format CHECK (iban IS NULL OR (iban ~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$'
                                                AND (left(iban, 2) <> 'EG' OR iban ~ '^EG[0-9]{27}$'))),
    CONSTRAINT bank_accounts_overdraft_chk CHECK ((account_type = 'overdraft') = (overdraft_limit IS NOT NULL)
                                                  AND (overdraft_limit IS NULL OR overdraft_limit > 0)),
    CONSTRAINT bank_accounts_maturity_chk CHECK (maturity_date IS NULL OR account_type = 'deposit')
);
CREATE UNIQUE INDEX bank_accounts_code_unique ON cash.bank_accounts (code);
CREATE UNIQUE INDEX bank_accounts_number_unique
    ON cash.bank_accounts (bank_id, upper(regexp_replace(account_number, '[^0-9A-Za-z]', '', 'g')))
    WHERE account_number IS NOT NULL;
CREATE UNIQUE INDEX bank_accounts_iban_unique ON cash.bank_accounts (iban) WHERE iban IS NOT NULL;
CREATE INDEX bank_accounts_bank_idx ON cash.bank_accounts (bank_id);
CREATE INDEX bank_accounts_currency_idx ON cash.bank_accounts (currency_code);
CREATE INDEX bank_accounts_active_idx ON cash.bank_accounts (is_active);
COMMENT ON TABLE cash.bank_accounts IS
    'الحسابات البنكية: كل حساب = حساب فرعي بدور «بنك» (تحت 1102) + النوع والعملة ورقم الحساب وIBAN · الكود BNK-001-01';

-- ---------------------------------------------------------------------------
-- اسم الحساب المتولّد: «<اسم البنك المختصر> — <النوع> [العملة لو مش الأساسية]»
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.bank_account_title(p_bank_label text, p_type text, p_currency text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT left(btrim(p_bank_label) || ' — ' ||
                CASE p_type WHEN 'current' THEN 'جاري' WHEN 'savings' THEN 'توفير'
                            WHEN 'deposit' THEN 'وديعة' WHEN 'overdraft' THEN 'جاري مدين' ELSE p_type END ||
                CASE WHEN EXISTS (SELECT 1 FROM core.currencies WHERE code = p_currency AND is_base) THEN ''
                     ELSE ' ' || p_currency END, 120)
$$;
COMMENT ON FUNCTION cash.bank_account_title(text, text, text) IS
    'اسم حساب الدليل المتولّد للحساب البنكي (البنك — النوع [العملة]) — نفس اللي في BankService.account_title';

-- ---------------------------------------------------------------------------
-- حماية البنك: الكود ثابت · مايتوقفش وعليه حساب نشط (المسح بيترفض بالـ FK لو عليه حسابات)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.banks_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked text;
BEGIN
    IF NEW.seq <> OLD.seq THEN
        RAISE EXCEPTION 'كود البنك «%» ثابت — مينفعش يتغيّر.', OLD.code;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active THEN
        SELECT string_agg(code, '، ' ORDER BY seq) INTO linked
          FROM cash.bank_accounts WHERE bank_id = OLD.id AND is_active;
        IF linked IS NOT NULL THEN
            RAISE EXCEPTION 'البنك «%» عليه حسابات نشطة (%) — وقّفها الأول.', OLD.name_ar, linked;
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER banks_guard_trg BEFORE UPDATE ON cash.banks
    FOR EACH ROW EXECUTE FUNCTION cash.banks_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.banks
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- اسم البنك اتغيّر ← أسماء حساباته في الدليل تتغيّر معاه (اللي كانت زي الاسم المتولّد بس).
CREATE FUNCTION cash.banks_sync_accounts() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_label text := coalesce(nullif(btrim(OLD.short_name), ''), OLD.name_ar);
    new_label text := coalesce(nullif(btrim(NEW.short_name), ''), NEW.name_ar);
BEGIN
    IF old_label IS DISTINCT FROM new_label THEN
        UPDATE acc.accounts a
           SET name_ar = cash.bank_account_title(new_label, b.account_type, b.currency_code)
          FROM cash.bank_accounts b
         WHERE b.bank_id = NEW.id AND a.id = b.account_id
           AND a.name_ar = cash.bank_account_title(old_label, b.account_type, b.currency_code);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER banks_sync_accounts_trg AFTER UPDATE ON cash.banks
    FOR EACH ROW EXECUTE FUNCTION cash.banks_sync_accounts();

-- ---------------------------------------------------------------------------
-- حماية الحساب البنكي (إضافة / تعديل / مسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.bank_accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    a  acc.accounts%ROWTYPE;
    b  cash.banks%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM acc.default_accounts WHERE key = 'bank' AND account_id = OLD.account_id) THEN
            RAISE EXCEPTION 'الحساب البنكي «%» هو البنك الافتراضي — غيّره من شاشة «الحسابات الافتراضية» الأول.',
                OLD.code;
        END IF;
        RETURN OLD;
    END IF;

    -- البنك (FOR SHARE: لو جهاز تاني بيوقف البنك في نفس اللحظة، واحد يستنى التاني)
    SELECT * INTO b FROM cash.banks WHERE id = NEW.bank_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'البنك مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF TG_OP = 'INSERT' THEN
        NEW.code := b.code || '-' || CASE WHEN NEW.seq < 100 THEN lpad(NEW.seq::text, 2, '0') ELSE NEW.seq::text END;
    ELSE
        IF NEW.seq <> OLD.seq OR NEW.bank_id <> OLD.bank_id OR NEW.code <> OLD.code THEN
            RAISE EXCEPTION 'كود الحساب البنكي «%» وبنكه ثابتين — مينفعش يتغيّروا.', OLD.code;
        END IF;
        IF NEW.account_id <> OLD.account_id OR NEW.owns_account <> OLD.owns_account THEN
            RAISE EXCEPTION 'حساب الدليل بتاع «%» ثابت بعد الحفظ — مينفعش يتغيّر.', OLD.code;
        END IF;
        NEW.updated_at := now();
    END IF;
    IF NEW.is_active AND NOT b.is_active AND (TG_OP = 'INSERT' OR NOT OLD.is_active) THEN
        RAISE EXCEPTION 'البنك «%» موقوف — نشّطه الأول.', b.name_ar;
    END IF;

    -- حساب الدليل: فرعي بدور «بنك»، ونشط لو الحساب البنكي نشط، وبنفس العملة (في الإضافة).
    SELECT * INTO a FROM acc.accounts WHERE id = NEW.account_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'حساب الدليل مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT a.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» رئيسي — الحساب البنكي لازم يبقى حسابه فرعي.', a.code, a.name_ar;
        END IF;
        IF a.role_code IS DISTINCT FROM 'bank' THEN
            RAISE EXCEPTION 'الحساب «% %» مش بدور «بنك» — اختار حساب تحت «البنوك».', a.code, a.name_ar;
        END IF;
        IF a.currency_code <> NEW.currency_code THEN
            RAISE EXCEPTION 'عملة الحساب «% %» (%) غير عملة الحساب البنكي (%).', a.code, a.name_ar, a.currency_code,
                NEW.currency_code;
        END IF;
    END IF;
    IF NEW.is_active AND NOT a.is_active AND (TG_OP = 'INSERT' OR NOT OLD.is_active) THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه من شجرة الحسابات الأول.', a.code, a.name_ar;
    END IF;

    -- البنك الافتراضي مايتوقفش.
    IF NOT NEW.is_active AND (TG_OP = 'INSERT' OR OLD.is_active)
       AND EXISTS (SELECT 1 FROM acc.default_accounts WHERE key = 'bank' AND account_id = NEW.account_id) THEN
        RAISE EXCEPTION 'الحساب البنكي «%» هو البنك الافتراضي — مينفعش يتوقف (غيّره من شاشة «الحسابات الافتراضية» الأول).',
            NEW.code;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bank_accounts_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON cash.bank_accounts
    FOR EACH ROW EXECUTE FUNCTION cash.bank_accounts_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.bank_accounts
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- بعد التعديل: عملة الحساب (دايماً) واسمه (لو كان زي الاسم المتولّد القديم) بيتغيّروا مع النوع / العملة.
CREATE FUNCTION cash.bank_accounts_sync_account() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    label     text;
    old_title text;
BEGIN
    IF NEW.currency_code IS DISTINCT FROM OLD.currency_code OR NEW.account_type IS DISTINCT FROM OLD.account_type THEN
        SELECT coalesce(nullif(btrim(short_name), ''), name_ar) INTO label FROM cash.banks WHERE id = NEW.bank_id;
        old_title := cash.bank_account_title(label, OLD.account_type, OLD.currency_code);
        PERFORM set_config('cash.sync_bank_account_id', NEW.account_id::text, true);
        UPDATE acc.accounts
           SET currency_code = NEW.currency_code,
               name_ar = CASE WHEN name_ar = old_title
                              THEN cash.bank_account_title(label, NEW.account_type, NEW.currency_code)
                              ELSE name_ar END
         WHERE id = NEW.account_id;
        PERFORM set_config('cash.sync_bank_account_id', '', true);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER bank_accounts_sync_account_trg
    AFTER UPDATE ON cash.bank_accounts
    FOR EACH ROW EXECUTE FUNCTION cash.bank_accounts_sync_account();

-- ---------------------------------------------------------------------------
-- حماية حساب الحساب البنكي من شجرة الحسابات: مايتمسحش ولا يتحوّل رئيسي (ولا يتفرّع) ولا يغيّر دوره،
-- ومايتوقفش والحساب البنكي نشط، وعملته من شاشة البنوك بس.
-- (الاسم accounts_zu… عشان رسايل النظامي والافتراضي تطلع الأول.)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_bank_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    b  cash.bank_accounts%ROWTYPE;
BEGIN
    SELECT * INTO b FROM cash.bank_accounts WHERE account_id = OLD.id;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالحساب البنكي «%» — امسح الحساب البنكي الأول.', OLD.code, OLD.name_ar, b.code;
    END IF;
    IF OLD.is_leaf AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالحساب البنكي «%» — مينفعش يتحوّل رئيسي ولا يتفرّع.',
            OLD.code, OLD.name_ar, b.code;
    END IF;
    IF NEW.role_code IS DISTINCT FROM OLD.role_code THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالحساب البنكي «%» — دوره «بنك» ثابت.', OLD.code, OLD.name_ar, b.code;
    END IF;
    IF OLD.is_active AND NOT NEW.is_active AND b.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالحساب البنكي «%» وهو نشط — وقّف الحساب البنكي الأول.',
            OLD.code, OLD.name_ar, b.code;
    END IF;
    IF NEW.currency_code <> OLD.currency_code
       AND coalesce(current_setting('cash.sync_bank_account_id', true), '') <> OLD.id::text THEN
        RAISE EXCEPTION 'الحساب «% %» مربوط بالحساب البنكي «%» — عملته بتتغيّر من شاشة البنوك بس.',
            OLD.code, OLD.name_ar, b.code;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER accounts_zubank_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_bank_guard();

-- ---------------------------------------------------------------------------
-- «البنك الافتراضي» (bank) في شاشة الحسابات الافتراضية لازم يبقى حساب بنكي نشط.
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.default_accounts_bank_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    b  cash.bank_accounts%ROWTYPE;
BEGIN
    IF NEW.key = 'bank' AND NEW.account_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
        SELECT * INTO b FROM cash.bank_accounts WHERE account_id = NEW.account_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '«البنك الافتراضي» لازم يبقى حساب بنكي — الحساب ده مش مربوط بحساب بنكي (اعمله من شاشة البنوك الأول).';
        END IF;
        IF NOT b.is_active THEN
            RAISE EXCEPTION 'الحساب البنكي «%» موقوف — نشّطه الأول عشان يبقى البنك الافتراضي.', b.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER default_accounts_zbank_trg
    BEFORE INSERT OR UPDATE ON acc.default_accounts
    FOR EACH ROW EXECUTE FUNCTION acc.default_accounts_bank_guard();

-- ---------------------------------------------------------------------------
-- البنكين الأولانيين + حساب جاري على كل واحد (لو حسابه لسه فرعي نشط بدور «بنك» في الدليل).
-- ---------------------------------------------------------------------------
INSERT INTO cash.banks (seq, name_ar, name_en, short_name, swift_code) VALUES
    (1, 'البنك الأهلي المصري', 'National Bank of Egypt', 'البنك الأهلي', 'NBEGEGCX'),
    (2, 'بنك مصر', 'Banque Misr', 'بنك مصر', 'BMISEGCX');

INSERT INTO cash.bank_accounts (bank_id, seq, code, account_type, currency_code, account_id)
SELECT b.id, 1, '', 'current', a.currency_code, a.id
  FROM (VALUES (1, '110201'), (2, '110202')) AS t (bank_seq, code)
  JOIN cash.banks b ON b.seq = t.bank_seq
  JOIN acc.accounts a ON a.code = t.code AND a.is_leaf AND a.is_active AND a.role_code = 'bank'
 ORDER BY t.bank_seq;
