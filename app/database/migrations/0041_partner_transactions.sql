-- 0041: «حركات الشركاء» (المرحلة 5 — النموذج 1 «الكلاسيك» — اختيار المستخدم 2026-11-22 —
-- mockups/phase5/05_partners_capital.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- ردود المستخدم (2026-11-22):
--   * النموذج 1: **شاشة واحدة للكل** بأربع أزرار نوع: إيداع شريك · سحب شريك · زيادة رأس المال · توزيع أرباح / خسارة.
--   * السحب والإيداع على **32 جاري الشركاء** ببُعد الشريك (مفيش حساب «مسحوبات» لوحده).
--   * توزيع الأرباح زي النموذج: الربح المتاح = الأرباح المحتجزة + ربح السنة من القيود · يوزّع كله أو جزء ·
--     **قبل إقفال السنة عادي** (المحتجزة ممكن تبقى بالسالب لحد قيد الإقفال) · بنسب الشركاء النشطين (مجموعها 100%).
--   * **فيه «توزيع خسارة» كمان** (تحميل الخسارة على جاري الشركاء بنسبهم).
--   * السحب أكبر من رصيد جاري الشريك ← يبقى «عليه» + تحذير بس.
--   * مسودة + ترحيل · الترقيم PD / PW / PC / PF · كشف حساب الشريك وقائمة حقوق الملكية مع التقارير ·
--     الأرصدة الافتتاحية للشركاء من شاشة «الأرصدة الافتتاحية» الموجودة · مفيش نقل من الأكسس · مكانها «الحسابات» ·
--     مفيش طباعة دلوقتي.
-- اقتراحاتي اللي مااتردش عليها (زي النموذج): زيادة رأس المال نقدي (خزنة / بنك) أو تحويل من جاري الشريك · المدفوع أكبر
--   من المكتتب ← تحذير بس · تخفيض رأس المال / خروج شريك اتأجّل (قيد يدوي لو حصل) · الخزنة مش مكفية ← تحذير بس ·
--   إلغاء الترحيل بالصلاحية · العملة الأساسية بس.
--
-- القيود (بمحرك الترحيل 0032 — مفيش حساب جديد في الدليل، بس مفتاح جديد `partner_cur` = 32 في الحسابات الافتراضية):
--   إيداع شريك:        مدين الخزنة / البنك             / دائن 32 جاري الشركاء (الشريك)
--   سحب شريك:          مدين 32 جاري الشركاء (الشريك)   / دائن الخزنة / البنك
--   زيادة رأس المال:   مدين الخزنة / البنك (أو 32 لو تحويل من الجاري) / دائن 31 رأس المال (الشريك)
--   توزيع أرباح:       مدين 33 الأرباح المحتجزة        / دائن 32 لكل شريك بنصيبه
--   توزيع خسارة:       مدين 32 لكل شريك بنصيبه         / دائن 33 الأرباح المحتجزة   (نفس القواعد بمبالغ سالبة)
-- نصيب كل شريك بيتحسب **وقت الترحيل** من نسبته في شاشة الشركاء (بقفل صفوف الشركاء) ويتسجل في سطور التوزيع —
--   والمسودة مالهاش سطور · إلغاء الترحيل بيمسح القيد والسطور.

-- ---------------------------------------------------------------------------
-- 1) مفتاح «جاري الشركاء» في الحسابات الافتراضية (الحساب = 32 بدور «جاري شريك»)
-- ---------------------------------------------------------------------------
ALTER TABLE acc.default_accounts DISABLE TRIGGER zz_row_version_trg;
UPDATE acc.default_accounts SET sort_order = sort_order + 1 WHERE sort_order >= 30;
INSERT INTO acc.default_accounts (key, group_code, name_ar, account_type, role_code, used_in, sort_order, account_id)
VALUES ('partner_cur', 'close', 'جاري الشركاء', 'equity', 'partner', 'سحب / إيداع شريك · توزيع الأرباح', 30,
        (SELECT id FROM acc.accounts WHERE role_code = 'partner' AND is_leaf AND is_active ORDER BY code LIMIT 1));
ALTER TABLE acc.default_accounts ENABLE TRIGGER zz_row_version_trg;

-- ---------------------------------------------------------------------------
-- 2) الترقيم + أنواع المستندات + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('partner_dep', 'إيداع شريك',           'PD', 5),
    ('partner_wd',  'سحب شريك',             'PW', 5),
    ('partner_cap', 'زيادة رأس المال',      'PC', 5),
    ('profit_dist', 'توزيع أرباح / خسارة',  'PF', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('partner_dep', 'إيداع شريك',          18),
    ('partner_wd',  'سحب شريك',            19),
    ('partner_cap', 'زيادة رأس المال',     20),
    ('profit_dist', 'توزيع أرباح / خسارة', 21);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('partner_dep', 'cash',     'الخزينة',            'dr', 'treasury', NULL,          NULL, NULL,      1),
    ('partner_dep', 'bank',     'البنك',              'dr', 'bank',     NULL,          NULL, NULL,      2),
    ('partner_dep', 'current',  'جاري الشركاء',       'cr', 'party',    'partner_cur', NULL, 'partner', 3),
    ('partner_wd',  'current',  'جاري الشركاء',       'dr', 'party',    'partner_cur', NULL, 'partner', 1),
    ('partner_wd',  'cash',     'الخزينة',            'cr', 'treasury', NULL,          NULL, NULL,      2),
    ('partner_wd',  'bank',     'البنك',              'cr', 'bank',     NULL,          NULL, NULL,      3),
    ('partner_cap', 'cash',     'الخزينة',            'dr', 'treasury', NULL,          NULL, NULL,      1),
    ('partner_cap', 'bank',     'البنك',              'dr', 'bank',     NULL,          NULL, NULL,      2),
    ('partner_cap', 'current',  'جاري الشركاء',       'dr', 'party',    'partner_cur', NULL, 'partner', 3),
    ('partner_cap', 'capital',  'رأس المال',          'cr', 'party',    'capital',     NULL, 'partner', 4),
    ('profit_dist', 'retained', 'الأرباح المحتجزة',   'dr', 'default',  'retained',    NULL, NULL,      1),
    ('profit_dist', 'current',  'جاري الشركاء',       'cr', 'party',    'partner_cur', NULL, 'partner', 2);

-- ---------------------------------------------------------------------------
-- 3) الجداول
-- ---------------------------------------------------------------------------
-- إيداع / سحب / زيادة رأس مال — لشريك واحد.
CREATE SEQUENCE cash.partner_transactions_draft_seq AS integer;
CREATE TABLE cash.partner_transactions (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    txn_no           varchar(30),                                     -- PD / PW / PC-2026-00001 (من أول ترحيل)
    draft_no         integer        NOT NULL DEFAULT nextval('cash.partner_transactions_draft_seq'),
    txn_kind         varchar(10)    NOT NULL,                         -- deposit إيداع · withdrawal سحب · capital رأس مال
    txn_date         date           NOT NULL,
    fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    partner_id       integer        NOT NULL REFERENCES party.partners (id) ON DELETE RESTRICT,
    source_kind      varchar(10)    NOT NULL,                         -- treasury · bank · current (رأس المال من الجاري)
    treasury_id      integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    amount           numeric(18,2)  NOT NULL,
    description      varchar(500)   NOT NULL,
    reference_no     varchar(40),                                     -- رقم الإيصال الورقي (اختياري)
    status           varchar(10)    NOT NULL DEFAULT 'draft',
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at        timestamptz,
    posted_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT partner_txn_row_version_chk CHECK (row_version > 0),
    CONSTRAINT partner_txn_no_unique UNIQUE (txn_no),
    CONSTRAINT partner_txn_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT partner_txn_kind_chk CHECK (txn_kind IN ('deposit', 'withdrawal', 'capital')),
    CONSTRAINT partner_txn_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT partner_txn_amount_chk CHECK (amount > 0),
    CONSTRAINT partner_txn_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT partner_txn_ref_chk CHECK (reference_no IS NULL OR btrim(reference_no) <> ''),
    CONSTRAINT partner_txn_source_chk CHECK (
        (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
        OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)
        OR (source_kind = 'current' AND txn_kind = 'capital' AND treasury_id IS NULL AND bank_account_id IS NULL)),
    CONSTRAINT partner_txn_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR txn_no IS NOT NULL))
);
ALTER SEQUENCE cash.partner_transactions_draft_seq OWNED BY cash.partner_transactions.draft_no;
COMMENT ON TABLE cash.partner_transactions IS
    'حركات الشركاء: إيداع / سحب على جاري الشركاء · زيادة رأس المال (نقدي أو من الجاري) · مسودة ← مرحّل بقيد أوتوماتيك';

CREATE INDEX partner_txn_date_idx ON cash.partner_transactions (txn_kind, txn_date DESC, id DESC);
CREATE INDEX partner_txn_partner_idx ON cash.partner_transactions (partner_id, status, txn_date);
CREATE INDEX partner_txn_year_idx ON cash.partner_transactions (fiscal_year_id, status);
CREATE INDEX partner_txn_treasury_idx ON cash.partner_transactions (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX partner_txn_bank_idx ON cash.partner_transactions (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX partner_txn_created_by_idx ON cash.partner_transactions (created_by);
CREATE INDEX partner_txn_posted_by_idx ON cash.partner_transactions (posted_by);
CREATE INDEX partner_txn_desc_idx ON cash.partner_transactions (lower(description) varchar_pattern_ops);

-- توزيع الأرباح / الخسارة على كل الشركاء النشطين بنسبهم.
CREATE SEQUENCE acc.profit_distributions_draft_seq AS integer;
CREATE TABLE acc.profit_distributions (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dist_no          varchar(30),                                     -- PF-2026-00001 (من أول ترحيل)
    draft_no         integer        NOT NULL DEFAULT nextval('acc.profit_distributions_draft_seq'),
    dist_kind        varchar(10)    NOT NULL DEFAULT 'profit',        -- profit توزيع ربح · loss تحميل خسارة
    dist_date        date           NOT NULL,
    fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    amount           numeric(18,2)  NOT NULL,
    description      varchar(500)   NOT NULL,
    reference_no     varchar(40),
    status           varchar(10)    NOT NULL DEFAULT 'draft',
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at        timestamptz,
    posted_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT profit_dist_row_version_chk CHECK (row_version > 0),
    CONSTRAINT profit_dist_no_unique UNIQUE (dist_no),
    CONSTRAINT profit_dist_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT profit_dist_kind_chk CHECK (dist_kind IN ('profit', 'loss')),
    CONSTRAINT profit_dist_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT profit_dist_amount_chk CHECK (amount > 0),
    CONSTRAINT profit_dist_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT profit_dist_ref_chk CHECK (reference_no IS NULL OR btrim(reference_no) <> ''),
    CONSTRAINT profit_dist_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR dist_no IS NOT NULL))
);
ALTER SEQUENCE acc.profit_distributions_draft_seq OWNED BY acc.profit_distributions.draft_no;
COMMENT ON TABLE acc.profit_distributions IS
    'توزيع الأرباح (مدين المحتجزة / دائن جاري كل شريك) أو تحميل الخسارة (العكس) بنسب الشركاء النشطين';

CREATE INDEX profit_dist_date_idx ON acc.profit_distributions (dist_date DESC, id DESC);
CREATE INDEX profit_dist_year_idx ON acc.profit_distributions (fiscal_year_id, status);
CREATE INDEX profit_dist_created_by_idx ON acc.profit_distributions (created_by);
CREATE INDEX profit_dist_posted_by_idx ON acc.profit_distributions (posted_by);
CREATE INDEX profit_dist_desc_idx ON acc.profit_distributions (lower(description) varchar_pattern_ops);

-- نصيب كل شريك (بيتسجل وقت الترحيل بس).
CREATE TABLE acc.profit_distribution_lines (
    distribution_id  integer        NOT NULL REFERENCES acc.profit_distributions (id) ON DELETE CASCADE,
    partner_id       integer        NOT NULL REFERENCES party.partners (id) ON DELETE RESTRICT,
    share_percent    numeric(5,2)   NOT NULL,
    amount           numeric(18,2)  NOT NULL,
    PRIMARY KEY (distribution_id, partner_id),
    CONSTRAINT profit_dist_lines_pct_chk CHECK (share_percent > 0 AND share_percent <= 100),
    CONSTRAINT profit_dist_lines_amount_chk CHECK (amount >= 0)
);
CREATE INDEX profit_dist_lines_partner_idx ON acc.profit_distribution_lines (partner_id);
COMMENT ON TABLE acc.profit_distribution_lines IS 'نصيب كل شريك في التوزيع (النسبة وقت الترحيل والمبلغ)';

-- ---------------------------------------------------------------------------
-- 4) الحماية: الشريك
-- ---------------------------------------------------------------------------
-- الإيداع وزيادة رأس المال لشريك نشط بس — والسحب مسموح للشريك اللي خرج (عشان ياخد رصيده).
CREATE FUNCTION cash.partner_txn_partner_check(p_partner integer, p_kind text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    m  record;
BEGIN
    SELECT code, name_ar, is_active INTO m FROM party.partners WHERE id = p_partner FOR SHARE;
    IF NOT m.is_active AND p_kind <> 'withdrawal' THEN
        RAISE EXCEPTION 'الشريك «% %» موقوف — الإيداع وزيادة رأس المال لشريك نشط بس (السحب مسموح).', m.code, m.name_ar;
    END IF;
END $$;

CREATE FUNCTION cash.partner_txn_doc_type(p_kind text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_kind WHEN 'deposit' THEN 'partner_dep' WHEN 'withdrawal' THEN 'partner_wd' ELSE 'partner_cap' END
$$;

-- ---------------------------------------------------------------------------
-- 5) حماية حركات الشركاء
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.partner_transactions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'الحركة ' || coalesce(OLD.txn_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — مستنداتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّلة — ماتتمسحش (الغي ترحيلها الأول).', lbl;
        END IF;
        IF OLD.txn_no IS NOT NULL THEN
            RAISE EXCEPTION '% خدت رقم رسمي قبل كده — ماتتمسحش عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.txn_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.txn_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل فيها حركة شريك.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.txn_no IS NOT NULL THEN
            RAISE EXCEPTION 'حركة الشريك الجديدة بتتسجل مسودة الأول (وبعدين تترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.txn_no IS NOT NULL AND NEW.txn_no IS DISTINCT FROM OLD.txn_no) THEN
            RAISE EXCEPTION 'رقم الحركة ثابت.';
        END IF;
        IF OLD.txn_no IS NOT NULL AND NEW.txn_kind <> OLD.txn_kind THEN
            RAISE EXCEPTION '% رقمها من نوع «%» — النوع مايتغيّرش (اعمل حركة جديدة).', lbl, OLD.txn_kind;
        END IF;
        IF OLD.txn_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمها من سنة % — تاريخها لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION '% مرحّلة — ماتتعدّلش (الغي ترحيلها الأول).', lbl;
                END IF;
                RETURN NEW;
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع الحركة مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NEW.txn_no IS NULL THEN
                NEW.txn_no := core.next_document_no(cash.partner_txn_doc_type(NEW.txn_kind), NEW.txn_date);
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    IF TG_OP = 'INSERT' OR posting OR NEW.partner_id <> OLD.partner_id OR NEW.txn_kind <> OLD.txn_kind THEN
        PERFORM cash.partner_txn_partner_check(NEW.partner_id, NEW.txn_kind);
    END IF;
    IF NEW.source_kind <> 'current' AND (TG_OP = 'INSERT' OR posting
       OR (NEW.treasury_id, NEW.bank_account_id) IS DISTINCT FROM (OLD.treasury_id, OLD.bank_account_id)) THEN
        PERFORM cash.cash_transfer_endpoint_check(NEW.source_kind, NEW.treasury_id, NEW.bank_account_id,
            CASE NEW.txn_kind WHEN 'withdrawal' THEN 'الصرف من' ELSE 'الاستلام في' END);
    END IF;
    RETURN NEW;
END $$;

-- المرحّلة لازم ليها قيد مرحّل بالمبلغ (بنوعها) والمسودة مالهاش قيد.
CREATE FUNCTION cash.partner_transactions_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v  record;
    e  record;
BEGIN
    SELECT id, status, amount, txn_kind, txn_no, draft_no INTO v FROM cash.partner_transactions WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT status, total_debit, source_type INTO e FROM acc.journal_entries
     WHERE source_type IN ('partner_dep', 'partner_wd', 'partner_cap') AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF e.status IS DISTINCT FROM 'posted' OR e.source_type <> cash.partner_txn_doc_type(v.txn_kind) THEN
            RAISE EXCEPTION 'الحركة % مرحّلة من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', v.txn_no;
        END IF;
        IF e.total_debit <> v.amount THEN
            RAISE EXCEPTION 'قيد الحركة % مبلغه % مش زي المبلغ %.', v.txn_no, e.total_debit, v.amount;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION 'الحركة % مسودة وليها قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.',
            coalesce(v.txn_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER partner_txn_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.partner_transactions
    FOR EACH ROW EXECUTE FUNCTION cash.partner_transactions_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.partner_transactions
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER partner_txn_entry_trg AFTER INSERT OR UPDATE ON cash.partner_transactions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.partner_transactions_entry_check();

-- ---------------------------------------------------------------------------
-- 6) حماية توزيع الأرباح / الخسارة
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.profit_distributions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'التوزيع ' || coalesce(OLD.dist_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — مستنداتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.dist_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.dist_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.dist_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها توزيع.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.dist_no IS NOT NULL THEN
            RAISE EXCEPTION 'التوزيع الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        RETURN NEW;
    END IF;

    IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR (OLD.dist_no IS NOT NULL AND NEW.dist_no IS DISTINCT FROM OLD.dist_no) THEN
        RAISE EXCEPTION 'رقم التوزيع ثابت.';
    END IF;
    IF OLD.dist_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
        RAISE EXCEPTION '% رقمه من سنة % — تاريخه لازم يفضل في نفس السنة.', lbl, oy.year_code;
    END IF;
    IF OLD.status = 'posted' THEN
        IF NEW.status = 'posted' THEN
            IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION '% مرحّل — مايتعدّلش (الغي ترحيله الأول).', lbl;
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
            RAISE EXCEPTION 'إلغاء الترحيل بيرجّع التوزيع مسودة بس — التعديل بعد كده.';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        NEW.updated_at := now();
        RETURN NEW;
    END IF;
    posting := NEW.status = 'posted';
    IF posting THEN
        IF NEW.dist_no IS NULL THEN
            NEW.dist_no := core.next_document_no('profit_dist', NEW.dist_date);
        END IF;
        NEW.posted_at := now();
    ELSE
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- السطور بتتغيّر والتوزيع مسودة بس (الخدمة بتكتبها قبل ما تغيّر الحالة لـ «مرحّل» وبتمسحها بعد إلغاء الترحيل).
CREATE FUNCTION acc.profit_distribution_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    s   record;
    did integer;
BEGIN
    did := CASE WHEN TG_OP = 'DELETE' THEN OLD.distribution_id ELSE NEW.distribution_id END;
    IF TG_OP = 'UPDATE' AND NEW.distribution_id <> OLD.distribution_id THEN
        RAISE EXCEPTION 'السطر مايتنقلش من توزيع لتوزيع.';
    END IF;
    SELECT id, status, coalesce(dist_no, 'مسودة ' || draft_no) AS lbl INTO s
      FROM acc.profit_distributions WHERE id = did;
    IF s.id IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- التوزيع نفسه بيتمسح
    END IF;
    IF s.status <> 'draft' THEN
        RAISE EXCEPTION 'التوزيع % مرحّل — أنصبة الشركاء ماتتغيّرش (الغي ترحيله الأول).', s.lbl;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- آخر الـ transaction: المرحّل ليه أنصبة مجموعها = المبلغ ونسبها = 100% وقيد بالمبلغ · المسودة من غير أنصبة ولا قيد.
CREATE FUNCTION acc.profit_distribution_check(p_id integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v  record;
    e  record;
    t  record;
BEGIN
    SELECT id, status, dist_no, draft_no, amount INTO v FROM acc.profit_distributions WHERE id = p_id;
    IF v.id IS NULL THEN
        RETURN;
    END IF;
    SELECT count(*) AS n, coalesce(sum(amount), 0) AS amt, coalesce(sum(share_percent), 0) AS pct INTO t
      FROM acc.profit_distribution_lines WHERE distribution_id = p_id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'profit_dist' AND source_id = p_id;
    IF v.status = 'posted' THEN
        IF t.n = 0 OR t.pct <> 100 THEN
            RAISE EXCEPTION 'التوزيع %: مجموع نسب الشركاء % مش 100%%.', v.dist_no, t.pct;
        END IF;
        IF t.amt <> v.amount THEN
            RAISE EXCEPTION 'التوزيع %: مجموع الأنصبة % مش زي المبلغ %.', v.dist_no, t.amt, v.amount;
        END IF;
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'التوزيع % مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', v.dist_no;
        END IF;
        IF e.total_debit <> v.amount THEN
            RAISE EXCEPTION 'قيد التوزيع % مبلغه % مش زي المبلغ %.', v.dist_no, e.total_debit, v.amount;
        END IF;
    ELSE
        IF e.status IS NOT NULL THEN
            RAISE EXCEPTION 'التوزيع % مسودة وليه قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.',
                coalesce(v.dist_no, 'مسودة ' || v.draft_no);
        END IF;
        IF t.n > 0 THEN
            RAISE EXCEPTION 'التوزيع % مسودة وعليه أنصبة — الأنصبة بتتحسب وقت الترحيل بس.',
                coalesce(v.dist_no, 'مسودة ' || v.draft_no);
        END IF;
    END IF;
END $$;

CREATE FUNCTION acc.profit_distributions_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM acc.profit_distribution_check(NEW.id);
    RETURN NULL;
END $$;

CREATE FUNCTION acc.profit_distribution_lines_check() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM acc.profit_distribution_check(CASE WHEN TG_OP = 'DELETE' THEN OLD.distribution_id
                                               ELSE NEW.distribution_id END);
    RETURN NULL;
END $$;

CREATE TRIGGER profit_dist_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON acc.profit_distributions
    FOR EACH ROW EXECUTE FUNCTION acc.profit_distributions_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.profit_distributions
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER profit_dist_entry_trg AFTER INSERT OR UPDATE ON acc.profit_distributions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION acc.profit_distributions_entry_check();
CREATE TRIGGER profit_dist_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON acc.profit_distribution_lines
    FOR EACH ROW EXECUTE FUNCTION acc.profit_distribution_lines_guard();
CREATE CONSTRAINT TRIGGER profit_dist_lines_check_trg AFTER INSERT OR UPDATE OR DELETE ON acc.profit_distribution_lines
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION acc.profit_distribution_lines_check();

-- ---------------------------------------------------------------------------
-- 7) السنة المالية: نفس حماية السندات والحركات والشيكات والعهد + حركات الشركاء والتوزيع
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  text[];
    c  integer;
    n  integer := 0;
    docs constant text[][] := ARRAY[['cash.customer_receipts', 'voucher_date'], ['cash.supplier_payments', 'voucher_date'],
                                    ['cash.customer_payments', 'voucher_date'], ['cash.cash_transfers', 'transfer_date'],
                                    ['cash.cheques', 'cheque_date'], ['cash.custody_disbursements', 'disb_date'],
                                    ['cash.custody_settlements', 'settle_date'], ['cash.partner_transactions', 'txn_date'],
                                    ['acc.profit_distributions', 'dist_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
                n, OLD.year_code;
        END IF;
    END IF;
    IF OLD.status = 'open' AND NEW.status = 'closed' THEN
        n := 0;
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND status = ''draft''', t[1])
                INTO c USING OLD.id;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
