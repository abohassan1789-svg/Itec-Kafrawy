-- 0031: القيود اليومية — «القيد اليدوي» (المرحلة 4، النموذج 1 «رأس القيد فوق + السطور + قائمة القيود شمال»
--       اختيار المستخدم 2026-11-16 — mockups/phase4/01_manual_journal.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (ردود 2026-11-16):
--   * زرارين: **«حفظ مسودة»** (تتحفظ حتى لو مش متوازنة) و**«ترحيل»** (لازم متوازن).
--   * **الرقم الرسمي** `JV-2026-00001` بيتاخد **وقت أول ترحيل** (من ترقيم المستندات 0006 — بيبدأ من 1 كل سنة
--     وجوه قفل)، والمسودة ليها رقم مؤقت «مسودة N». المسلسل بعد 99,999 بيكمّل من غير ما يتقص (1,000,000 عادي).
--     القيد اللي اتلغى ترحيله **بيفضل برقمه**، ومايتمسحش (عشان مايبقاش فيه فجوة في الأرقام) — يتعدّل ويترحّل تاني.
--   * **إلغاء الترحيل** بالصلاحية بس (من غير كلمة سر): القيد بيرجع مسودة بسطوره ويتعدّل — في السنة المفتوحة بس.
--   * **مفيش عكس** (رد المستخدم: «أنا مش عايز عكس»).
--   * **الطرف إجباري** على حسابات الرقابة (acc.accounts.party_kind: العملاء ← عميل · الموردين ← مورد ·
--     العهد ← موظف · جاري الشركاء ← شريك)، والحساب اللي مالوش طرف مايتكتبش عليه طرف.
--   * **مركز التكلفة اختياري** (إلا لو الحساب «لازم مركز تكلفة») · **أمر التوريد** اختياري.
--   * **بالجنيه المصري بس** (القيود اليدوية والأوتوماتيك).
--   * القيود الأوتوماتيك (من الفواتير والسندات — لما ييجوا) في نفس الجداول بـ `source_type` وبتظهر **قراية بس**.
--   * مفيش نسخ ولا طباعة دلوقتي.
-- الحماية في القاعدة نفسها (المبدأ: «القاعدة بتحمي التوازن والقفل»):
--   * المرحّل لازم: سطرين على الأقل · المدين = الدائن > 0 · كل الحسابات فرعية نشطة · السنة مفتوحة.
--   * المرحّل مايتعدّلش ولا يتمسح ولا سطوره (غير إلغاء الترحيل) · مفيش أي تعديل في سنة مقفولة.
--   * السطر: مدين **أو** دائن (مش الاتنين) · طرف واحد بالكتير ومن نوع الحساب.
--   * الحساب / مركز التكلفة اللي عليه قيود مايتمسحش، ومايتحوّلش رئيسي غير بالتفريع (والسطور بتنزل على «— عام» —
--     قرار المستخدم (أ) 2026-11-08) · السنة اللي عليها قيود ماتتمسحش وتواريخها ماتسيبش قيد برّاها، وماتتقفلش وفيها مسودات.

-- ---------------------------------------------------------------------------
-- رأس القيد
-- ---------------------------------------------------------------------------
CREATE SEQUENCE acc.journal_draft_seq AS integer;
COMMENT ON SEQUENCE acc.journal_draft_seq IS 'رقم المسودة المؤقت («مسودة N») — الرقم الرسمي JV بيتاخد مع الترحيل';

CREATE TABLE acc.journal_entries (
    id              integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_no        varchar(30),                                    -- JV-2026-00001 (من أول ترحيل)
    draft_no        integer        NOT NULL DEFAULT nextval('acc.journal_draft_seq'),
    entry_date      date           NOT NULL,
    fiscal_year_id  integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,  -- من التاريخ
    description     varchar(500)   NOT NULL,
    reference       varchar(100),
    status          varchar(10)    NOT NULL DEFAULT 'draft',
    source_type     varchar(30)    NOT NULL DEFAULT 'manual',      -- manual = يدوي · غيره = أوتوماتيك من مستند
    source_id       integer,
    source_no       varchar(40),
    total_debit     numeric(18,2)  NOT NULL DEFAULT 0,
    total_credit    numeric(18,2)  NOT NULL DEFAULT 0,
    lines_count     integer        NOT NULL DEFAULT 0,
    created_by      integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at       timestamptz,
    posted_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at      timestamptz    NOT NULL DEFAULT now(),
    updated_at      timestamptz    NOT NULL DEFAULT now(),
    row_version     integer        NOT NULL DEFAULT 1 CONSTRAINT journal_entries_row_version_chk CHECK (row_version > 0),
    CONSTRAINT journal_entries_no_unique UNIQUE (entry_no),
    CONSTRAINT journal_entries_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT journal_entries_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT journal_entries_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT journal_entries_source_chk CHECK ((source_type = 'manual') = (source_id IS NULL)),
    CONSTRAINT journal_entries_totals_chk CHECK (total_debit >= 0 AND total_credit >= 0 AND lines_count >= 0),
    CONSTRAINT journal_entries_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL)
        AND (status = 'draft' OR (total_debit = total_credit AND total_debit > 0 AND lines_count >= 2))),
    -- القيد اليدوي المرحّل لازم ليه رقم رسمي.
    CONSTRAINT journal_entries_number_chk CHECK (status = 'draft' OR source_type <> 'manual' OR entry_no IS NOT NULL)
);
ALTER SEQUENCE acc.journal_draft_seq OWNED BY acc.journal_entries.draft_no;
-- القائمة بالفترة (الأحدث فوق) — مع مليون قيد الفلتر بالتاريخ والصفحات من الـ index ده.
CREATE INDEX journal_entries_date_idx ON acc.journal_entries (entry_date DESC, id DESC);
CREATE INDEX journal_entries_year_idx ON acc.journal_entries (fiscal_year_id, status);
CREATE INDEX journal_entries_drafts_idx ON acc.journal_entries (entry_date) WHERE status = 'draft';
CREATE INDEX journal_entries_created_by_idx ON acc.journal_entries (created_by);
CREATE INDEX journal_entries_posted_by_idx ON acc.journal_entries (posted_by);
CREATE INDEX journal_entries_desc_idx ON acc.journal_entries (lower(description) varchar_pattern_ops);
-- قيد واحد بس لكل مستند (الفاتورة / السند) — لما محرك الترحيل ييجي.
CREATE UNIQUE INDEX journal_entries_source_unique ON acc.journal_entries (source_type, source_id)
    WHERE source_type <> 'manual';
COMMENT ON TABLE acc.journal_entries IS
    'رأس قيد اليومية: مسودة ← مرحّل (← إلغاء ترحيل = مسودة) · الرقم JV من أول ترحيل · يدوي أو من مستند (source_type)';

-- ---------------------------------------------------------------------------
-- سطور القيد
-- ---------------------------------------------------------------------------
CREATE TABLE acc.journal_lines (
    id              bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id        integer        NOT NULL REFERENCES acc.journal_entries (id) ON DELETE CASCADE,
    line_no         smallint       NOT NULL,
    account_id      integer        NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    description     varchar(300),
    debit           numeric(18,2)  NOT NULL DEFAULT 0,
    credit          numeric(18,2)  NOT NULL DEFAULT 0,
    cost_center_id  integer        REFERENCES acc.cost_centers (id) ON DELETE RESTRICT,
    project_id      integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,
    customer_id     integer        REFERENCES party.customers (id) ON DELETE RESTRICT,
    supplier_id     integer        REFERENCES party.suppliers (id) ON DELETE RESTRICT,
    employee_id     integer        REFERENCES party.employees (id) ON DELETE RESTRICT,
    partner_id      integer        REFERENCES party.partners (id) ON DELETE RESTRICT,
    CONSTRAINT journal_lines_no_unique UNIQUE (entry_id, line_no),
    CONSTRAINT journal_lines_no_chk CHECK (line_no > 0),
    CONSTRAINT journal_lines_amount_chk CHECK (debit >= 0 AND credit >= 0 AND (debit > 0) <> (credit > 0)),
    CONSTRAINT journal_lines_one_party_chk CHECK (
        num_nonnulls(customer_id, supplier_id, employee_id, partner_id) <= 1)
);
CREATE INDEX journal_lines_account_idx ON acc.journal_lines (account_id, entry_id);
CREATE INDEX journal_lines_cost_center_idx ON acc.journal_lines (cost_center_id) WHERE cost_center_id IS NOT NULL;
CREATE INDEX journal_lines_project_idx ON acc.journal_lines (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX journal_lines_customer_idx ON acc.journal_lines (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX journal_lines_supplier_idx ON acc.journal_lines (supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX journal_lines_employee_idx ON acc.journal_lines (employee_id) WHERE employee_id IS NOT NULL;
CREATE INDEX journal_lines_partner_idx ON acc.journal_lines (partner_id) WHERE partner_id IS NOT NULL;
COMMENT ON TABLE acc.journal_lines IS
    'سطور القيد: حساب فرعي + مدين أو دائن + أبعاد (مركز تكلفة · أمر توريد · طرف واحد من نوع الحساب)';

-- ---------------------------------------------------------------------------
-- فحص السطر (حساب فرعي نشط · الطرف من نوع الحساب · مركز التكلفة فرعي نشط · أمر التوريد جاري)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.journal_line_check(l acc.journal_lines, o acc.journal_lines) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    a        acc.accounts%ROWTYPE;
    cc       acc.cost_centers%ROWTYPE;
    pj       acc.projects%ROWTYPE;
    p_id     integer;
    p_name   text;
    p_active boolean;
    kinds    constant text[] := ARRAY['customer', 'supplier', 'employee', 'partner'];
    labels   constant text[] := ARRAY['العميل', 'المورد', 'الموظف', 'الشريك'];
    ids      integer[] := ARRAY[l.customer_id, l.supplier_id, l.employee_id, l.partner_id];
    i        integer;
BEGIN
    -- FOR SHARE: لو جهاز تاني بيفرّع الحساب أو بيوقفه في نفس اللحظة، واحد يستنى التاني.
    SELECT * INTO a FROM acc.accounts WHERE id = l.account_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'السطر %: الحساب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».', l.line_no;
    END IF;
    IF NOT a.is_leaf THEN
        RAISE EXCEPTION 'السطر %: الحساب «% %» رئيسي — القيد على الحسابات الفرعية بس.', l.line_no, a.code, a.name_ar;
    END IF;
    IF NOT a.is_active AND (o IS NULL OR o.account_id IS DISTINCT FROM l.account_id) THEN
        RAISE EXCEPTION 'السطر %: الحساب «% %» موقوف — اختار حساب نشط.', l.line_no, a.code, a.name_ar;
    END IF;

    -- الطرف: إجباري لو الحساب رقابة ومن نوعه بس · ممنوع لو الحساب مالوش طرف.
    FOR i IN 1..4 LOOP
        IF ids[i] IS NOT NULL AND kinds[i] IS DISTINCT FROM a.party_kind THEN
            IF a.party_kind IS NULL THEN
                RAISE EXCEPTION 'السطر %: الحساب «% %» مالوش طرف — شيل % من السطر.', l.line_no, a.code, a.name_ar, labels[i];
            END IF;
            RAISE EXCEPTION 'السطر %: الحساب «% %» طرفه % — مش %.', l.line_no, a.code, a.name_ar,
                labels[array_position(kinds, a.party_kind)], labels[i];
        END IF;
    END LOOP;
    IF a.party_kind IS NOT NULL THEN
        i := array_position(kinds, a.party_kind);
        p_id := ids[i];
        IF p_id IS NULL THEN
            RAISE EXCEPTION 'السطر %: الحساب «% %» حساب رقابة — لازم تختار %.', l.line_no, a.code, a.name_ar, labels[i];
        END IF;
        IF o IS NULL OR ARRAY[o.customer_id, o.supplier_id, o.employee_id, o.partner_id] IS DISTINCT FROM ids THEN
            CASE a.party_kind
                WHEN 'customer' THEN SELECT name_ar, is_active INTO p_name, p_active FROM party.customers WHERE id = p_id;
                WHEN 'supplier' THEN SELECT name_ar, is_active INTO p_name, p_active FROM party.suppliers WHERE id = p_id;
                WHEN 'employee' THEN SELECT name_ar, is_active INTO p_name, p_active FROM party.employees WHERE id = p_id;
                ELSE SELECT name_ar, is_active INTO p_name, p_active FROM party.partners WHERE id = p_id;
            END CASE;
            IF NOT p_active THEN
                RAISE EXCEPTION 'السطر %: % «%» موقوف — اختار واحد نشط.', l.line_no, labels[i], p_name;
            END IF;
        END IF;
    END IF;

    -- مركز التكلفة: اختياري — إلا لو الحساب «لازم مركز تكلفة».
    IF l.cost_center_id IS NULL THEN
        IF a.requires_cost_center THEN
            RAISE EXCEPTION 'السطر %: الحساب «% %» لازم يتكتب عليه مركز تكلفة.', l.line_no, a.code, a.name_ar;
        END IF;
    ELSE
        SELECT * INTO cc FROM acc.cost_centers WHERE id = l.cost_center_id FOR SHARE;
        IF NOT cc.is_leaf THEN
            RAISE EXCEPTION 'السطر %: مركز التكلفة «% %» رئيسي — اختار مركز فرعي.', l.line_no, cc.code, cc.name_ar;
        END IF;
        IF NOT cc.is_active AND (o IS NULL OR o.cost_center_id IS DISTINCT FROM l.cost_center_id) THEN
            RAISE EXCEPTION 'السطر %: مركز التكلفة «% %» موقوف.', l.line_no, cc.code, cc.name_ar;
        END IF;
    END IF;

    -- أمر التوريد: اختياري — الجديد لازم «جاري».
    IF l.project_id IS NOT NULL AND (o IS NULL OR o.project_id IS DISTINCT FROM l.project_id) THEN
        SELECT * INTO pj FROM acc.projects WHERE id = l.project_id;
        IF pj.status <> 'open' THEN
            RAISE EXCEPTION 'السطر %: أمر التوريد «%» مش جاري (مقفول أو ملغي).', l.line_no, pj.code;
        END IF;
    END IF;
END $$;

CREATE FUNCTION acc.journal_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    e       acc.journal_entries%ROWTYPE;
    moving  boolean := coalesce(current_setting('acc.journal_line_move', true), '') = 'on';
BEGIN
    SELECT * INTO e FROM acc.journal_entries
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.entry_id ELSE NEW.entry_id END;
    IF TG_OP = 'DELETE' THEN
        IF FOUND AND e.status = 'posted' THEN
            RAISE EXCEPTION 'القيد % مرحّل — سطوره ماتتمسحش (الغي ترحيله الأول).', coalesce(e.entry_no, e.draft_no::text);
        END IF;
        RETURN OLD;                                     -- (ولو الرأس اتمسح = المسح جاي منه وهو اتفحص)
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'القيد ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    -- التفريع (acc.split_account / acc.split_cost_center): نقل الحساب أو المركز للـ «— عام» بس.
    IF TG_OP = 'UPDATE' AND moving THEN
        IF (to_jsonb(NEW) - 'account_id' - 'cost_center_id') IS DISTINCT FROM
           (to_jsonb(OLD) - 'account_id' - 'cost_center_id') THEN
            RAISE EXCEPTION 'نقل سطور القيود مع التفريع بيغيّر الحساب ومركز التكلفة بس.';
        END IF;
        RETURN NEW;
    END IF;
    IF e.status = 'posted' THEN
        RAISE EXCEPTION 'القيد % مرحّل — سطوره ماتتعدّلش (الغي ترحيله الأول).', coalesce(e.entry_no, e.draft_no::text);
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.entry_id <> OLD.entry_id THEN
        RAISE EXCEPTION 'سطر القيد مايتنقلش لقيد تاني.';
    END IF;
    PERFORM acc.journal_line_check(NEW, CASE WHEN TG_OP = 'UPDATE' THEN OLD END);
    RETURN NEW;
END $$;
CREATE TRIGGER journal_lines_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON acc.journal_lines
    FOR EACH ROW EXECUTE FUNCTION acc.journal_lines_guard();

-- ---------------------------------------------------------------------------
-- حماية رأس القيد (السنة · الترحيل · إلغاء الترحيل · المسح)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.journal_entries_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    n        integer;
    d        numeric;
    c        numeric;
    bad      record;
    label    text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        label := coalesce(OLD.entry_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — قيودها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION 'القيد % مرحّل — مايتمسحش (الغي ترحيله الأول).', label;
        END IF;
        IF OLD.entry_no IS NOT NULL THEN
            RAISE EXCEPTION 'القيد % خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', label;
        END IF;
        RETURN OLD;
    END IF;

    -- السنة المالية من التاريخ (لازم مفتوحة).
    SELECT * INTO y FROM core.fiscal_years
     WHERE NEW.entry_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.entry_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها قيد.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.entry_no IS NOT NULL THEN
            RAISE EXCEPTION 'القيد الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        RETURN NEW;
    END IF;

    -- ---- UPDATE ----
    IF NEW.draft_no <> OLD.draft_no OR NEW.source_type <> OLD.source_type
       OR NEW.source_id IS DISTINCT FROM OLD.source_id OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.entry_no IS DISTINCT FROM OLD.entry_no AND OLD.entry_no IS NOT NULL THEN
        RAISE EXCEPTION 'رقم القيد ومصدره ثابتين.';
    END IF;
    IF OLD.entry_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
        RAISE EXCEPTION 'القيد % رقمه من سنة % — تاريخه لازم يفضل في نفس السنة.', label, oy.year_code;
    END IF;

    IF OLD.status = 'posted' THEN
        IF NEW.status = 'posted' THEN
            IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'القيد % مرحّل — مايتعدّلش (الغي ترحيله الأول).', label;
            END IF;
            RETURN NEW;
        END IF;
        -- إلغاء الترحيل: الحالة بس (القيد بيرجع مسودة برقمه وسطوره).
        IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
            RAISE EXCEPTION 'إلغاء الترحيل بيرجّع القيد مسودة بس — التعديل بعد كده.';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    IF NEW.status = 'posted' THEN
        -- الترحيل: متوازن · سطرين على الأقل · كل حاجة لسه نشطة وفرعية.
        SELECT count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0) INTO n, d, c
          FROM acc.journal_lines WHERE entry_id = NEW.id;
        IF n < 2 THEN
            RAISE EXCEPTION 'القيد لازم يبقى فيه سطرين على الأقل (مدين ودائن) عشان يترحّل.';
        END IF;
        IF d <> c THEN
            RAISE EXCEPTION 'القيد مش متوازن (المدين % ≠ الدائن %، الفرق %) — مايترحّلش.',
                to_char(d, 'FM999,999,999,999,990.00'), to_char(c, 'FM999,999,999,999,990.00'),
                to_char(abs(d - c), 'FM999,999,999,999,990.00');
        END IF;
        SELECT l.line_no, a.code, a.name_ar,
               CASE WHEN NOT a.is_leaf THEN 'رئيسي' ELSE 'موقوف' END AS why
          INTO bad
          FROM acc.journal_lines l JOIN acc.accounts a ON a.id = l.account_id
         WHERE l.entry_id = NEW.id AND (NOT a.is_leaf OR NOT a.is_active)
         ORDER BY l.line_no LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'السطر %: الحساب «% %» % — مايترحّلش عليه.', bad.line_no, bad.code, bad.name_ar, bad.why;
        END IF;
        SELECT l.line_no, cc.code, cc.name_ar INTO bad
          FROM acc.journal_lines l JOIN acc.cost_centers cc ON cc.id = l.cost_center_id
         WHERE l.entry_id = NEW.id AND (NOT cc.is_leaf OR NOT cc.is_active)
         ORDER BY l.line_no LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'السطر %: مركز التكلفة «% %» موقوف أو رئيسي — مايترحّلش عليه.', bad.line_no, bad.code, bad.name_ar;
        END IF;
        NEW.total_debit := d;
        NEW.total_credit := c;
        NEW.lines_count := n;
        IF NEW.entry_no IS NULL AND NEW.source_type = 'manual' THEN
            NEW.entry_no := core.next_document_no('journal', NEW.entry_date);   -- جوه قفل العدّاد
        END IF;
        NEW.posted_at := now();
    ELSE
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER journal_entries_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON acc.journal_entries
    FOR EACH ROW EXECUTE FUNCTION acc.journal_entries_guard();

-- حماية التعديل في نفس الوقت (المرحلة 1ج — زي 0008).
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.journal_entries
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- السنة المالية: اللي عليها قيود ماتتمسحش · تواريخها ماتسيبش قيد برّاها · ماتتقفلش وفيها مسودات
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.fiscal_years_journal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT count(*) INTO n FROM acc.journal_entries WHERE fiscal_year_id = OLD.id;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % عليها % قيد — ماتتمسحش.', OLD.year_code, n;
        END IF;
        RETURN OLD;
    END IF;
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        SELECT count(*) INTO n FROM acc.journal_entries
         WHERE fiscal_year_id = OLD.id AND entry_date NOT BETWEEN NEW.start_date AND NEW.end_date;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % قيد في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.', n, OLD.year_code;
        END IF;
    END IF;
    IF OLD.status = 'open' AND NEW.status = 'closed' THEN
        SELECT count(*) INTO n FROM acc.journal_entries WHERE fiscal_year_id = OLD.id AND status = 'draft';
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % قيد مسودة — رحّلهم أو امسحهم قبل القفل.', OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fiscal_years_zjournal_guard_trg
    BEFORE UPDATE OR DELETE ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_journal_guard();

-- ---------------------------------------------------------------------------
-- الحساب / مركز التكلفة اللي عليه قيود: مايتمسحش · مايتحوّلش رئيسي غير بالتفريع · نوع الطرف ثابت
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.accounts_journal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NOT (OLD.is_leaf AND NOT NEW.is_leaf)
       AND NEW.party_kind IS NOT DISTINCT FROM OLD.party_kind THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO n FROM acc.journal_lines WHERE account_id = OLD.id;
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الحساب «% %» عليه % سطر في القيود — مايتمسحش (وقّفه بدل كده).', OLD.code, OLD.name_ar, n;
    END IF;
    IF NEW.party_kind IS DISTINCT FROM OLD.party_kind THEN
        RAISE EXCEPTION 'الحساب «% %» عليه قيود — نوع الطرف بتاعه مايتغيّرش.', OLD.code, OLD.name_ar;
    END IF;
    IF coalesce(current_setting('acc.split_account_id', true), '') = OLD.id::text THEN
        RETURN NEW;                                     -- التفريع بينقل السطور للـ «— عام»
    END IF;
    RAISE EXCEPTION 'الحساب «% %» عليه قيود — مينفعش يتحوّل رئيسي من «تعديل»؛ استخدم «+ فرعي تحت المحدد» والقيود هتنزل على «— عام».',
        OLD.code, OLD.name_ar;
END $$;
CREATE TRIGGER accounts_zjournal_guard_trg
    BEFORE UPDATE OR DELETE ON acc.accounts
    FOR EACH ROW EXECUTE FUNCTION acc.accounts_journal_guard();

CREATE FUNCTION acc.cost_centers_journal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NOT (OLD.is_leaf AND NOT NEW.is_leaf) THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO n FROM acc.journal_lines WHERE cost_center_id = OLD.id;
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'المركز «% %» عليه % سطر في القيود — وقّفه بدل ما تمسحه.', OLD.code, OLD.name_ar, n;
    END IF;
    IF coalesce(current_setting('acc.split_cost_center_id', true), '') = OLD.id::text THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'المركز «% %» عليه قيود — مينفعش يتحوّل رئيسي من «تعديل»؛ استخدم «+ فرعي تحت المحدد» والقيود هتنزل على «— عام».',
        OLD.code, OLD.name_ar;
END $$;
CREATE TRIGGER cost_centers_zjournal_guard_trg
    BEFORE UPDATE OR DELETE ON acc.cost_centers
    FOR EACH ROW EXECUTE FUNCTION acc.cost_centers_journal_guard();

-- ---------------------------------------------------------------------------
-- التفريع: سطور القيود بتنزل على «— عام» (قرار المستخدم (أ) — نقطة الـ TODO في 0011 ← 0028 و0013 ← 0016 اتقفلت).
-- الدالة القديمة اتسمّت `_core` بنفس كودها، والجديدة بتناديها وبعدين تنقل السطور في نفس الـ transaction.
-- ⚠️ أي سكريبت جاي بيعدّل التفريع يعدّل `acc.split_account_core` / `acc.split_cost_center_core`.
-- ---------------------------------------------------------------------------
ALTER FUNCTION acc.split_account(integer, varchar) RENAME TO split_account_core;
CREATE FUNCTION acc.split_account(p_account_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم الحساب «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    v_general integer;
BEGIN
    v_general := acc.split_account_core(p_account_id, p_general_code);
    PERFORM set_config('acc.journal_line_move', 'on', true);
    UPDATE acc.journal_lines SET account_id = v_general WHERE account_id = p_account_id;
    PERFORM set_config('acc.journal_line_move', '', true);
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_account(integer, varchar) IS
    'تفريع حساب فرعي: split_account_core (يتحوّل رئيسي + «— عام» بكل الروابط) + سطور القيود بتنزل على «— عام»';

ALTER FUNCTION acc.split_cost_center(integer, varchar) RENAME TO split_cost_center_core;
CREATE FUNCTION acc.split_cost_center(p_center_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم المركز «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    v_general integer;
BEGIN
    v_general := acc.split_cost_center_core(p_center_id, p_general_code);
    PERFORM set_config('acc.journal_line_move', 'on', true);
    UPDATE acc.journal_lines SET cost_center_id = v_general WHERE cost_center_id = p_center_id;
    PERFORM set_config('acc.journal_line_move', '', true);
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_cost_center(integer, varchar) IS
    'تفريع مركز فرعي: split_cost_center_core (يتحوّل رئيسي + «— عام» وأوامر التوريد) + سطور القيود بتنزل على «— عام»';
