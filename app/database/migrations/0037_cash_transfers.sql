-- 0037: «حركات الخزائن والبنوك» (المرحلة 5 — النموذج 2 «تبويب لكل نوع حركة»، اختيار المستخدم 2026-11-17
--       — mockups/phase5/02_cash_movements.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (ردود 2026-11-17):
--   * الحركة = نقل فلوس بين الخزائن والبنوك بتوعنا: **من** (خزنة / حساب بنك) ← **إلى** (خزنة / حساب بنك)،
--     والنوع بيطلع لوحده: إيداع في البنك (خزنة ← بنك) · سحب من البنك (بنك ← خزنة) · بين خزنتين · بين بنكين.
--   * **مفيش «إضافة أموال للخزينة»** (Fmaalk في الأكسس) — اتشالت خالص.
--   * الرصيد مش مكفي ← **تحذير بس** (من الشاشة — مش منع).
--   * **«حفظ مسودة» و«ترحيل»** زي السندات · إلغاء الترحيل بالصلاحية في السنة المفتوحة.
-- اقتراحاتي (مااتردش عليها — بقت زي النموذج 2): مصاريف بنكية اختيارية في نفس الحركة (على «مصاريف بنكية» الافتراضي)
--   · الترقيم CT-2026-00001 من أول ترحيل (المسودة «مسودة N») · «رقم الإيصال» اختياري · العملة الأساسية بس
--   · خطوة واحدة (من غير «نقدية في الطريق») · الطباعة بعدين.
-- القيد (بمحرك الترحيل 0032): مدين «إلى» بالمبلغ · مدين مصاريف بنكية بالمصاريف · دائن «من» بالمبلغ + المصاريف.
-- الحماية في القاعدة نفسها (زي السندات):
--   * السنة من التاريخ ولازم مفتوحة · المرحّل مايتعدّلش ولا يتمسح (غير إلغاء الترحيل) · الرقم ثابت وفي نفس السنة.
--   * «من» غير «إلى» · الخزنة / الحساب البنكي نشط وبالعملة الأساسية وقت الإضافة أو التغيير أو الترحيل.
--   * **المرحّل لازم ليه قيد مرحّل بالمبلغ + المصاريف والمسودة مالهاش قيد** (Constraint Trigger مؤجل).
--   * السنة ماتتقفلش وفيها حركات مسودة، وتواريخها ماتسيبش حركة برّاها.

-- ---------------------------------------------------------------------------
-- 1) الترقيم + نوع المستند + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('cash_transfer', 'حركة خزائن وبنوك', 'CT', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('cash_transfer', 'حركة خزائن وبنوك', 13);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('cash_transfer', 'to_cash',   'الخزنة المستلِمة', 'dr', 'treasury', NULL,           NULL, NULL, 1),
    ('cash_transfer', 'to_bank',   'البنك المستلِم',   'dr', 'bank',     NULL,           NULL, NULL, 2),
    ('cash_transfer', 'fees',      'مصاريف بنكية',     'dr', 'default',  'bank_charges', NULL, NULL, 3),
    ('cash_transfer', 'from_cash', 'الخزنة المرسِلة',  'cr', 'treasury', NULL,           NULL, NULL, 4),
    ('cash_transfer', 'from_bank', 'البنك المرسِل',    'cr', 'bank',     NULL,           NULL, NULL, 5);

-- ---------------------------------------------------------------------------
-- 2) الجدول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE cash.cash_transfers_draft_seq AS integer;
CREATE TABLE cash.cash_transfers (
    id                    integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transfer_no           varchar(30),                                   -- CT-2026-00001 (من أول ترحيل)
    draft_no              integer        NOT NULL DEFAULT nextval('cash.cash_transfers_draft_seq'),
    transfer_date         date           NOT NULL,
    fiscal_year_id        integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    transfer_type         varchar(10)    NOT NULL,                       -- deposit · withdraw · t2t · b2b (من «من» و«إلى»)
    from_kind             varchar(10)    NOT NULL,                       -- treasury خزنة · bank حساب بنك
    from_treasury_id      integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    from_bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    to_kind               varchar(10)    NOT NULL,
    to_treasury_id        integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    to_bank_account_id    integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    amount                numeric(18,2)  NOT NULL,                       -- اللي وصل «إلى»
    fees                  numeric(18,2)  NOT NULL DEFAULT 0,             -- مصاريف بنكية (بتتخصم من «من» زيادة)
    description           varchar(500)   NOT NULL,
    reference_no          varchar(40),                                   -- رقم الإيصال / التحويل (اختياري)
    status                varchar(10)    NOT NULL DEFAULT 'draft',
    created_by            integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at             timestamptz,
    posted_by             integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at            timestamptz    NOT NULL DEFAULT now(),
    updated_at            timestamptz    NOT NULL DEFAULT now(),
    row_version           integer        NOT NULL DEFAULT 1 CONSTRAINT cash_transfers_row_version_chk CHECK (row_version > 0),
    CONSTRAINT cash_transfers_no_unique UNIQUE (transfer_no),
    CONSTRAINT cash_transfers_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT cash_transfers_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT cash_transfers_amount_chk CHECK (amount > 0),
    CONSTRAINT cash_transfers_fees_chk CHECK (fees >= 0),
    CONSTRAINT cash_transfers_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT cash_transfers_ref_chk CHECK (reference_no IS NULL OR btrim(reference_no) <> ''),
    CONSTRAINT cash_transfers_from_chk CHECK (
        (from_kind = 'treasury' AND from_treasury_id IS NOT NULL AND from_bank_account_id IS NULL)
        OR (from_kind = 'bank' AND from_bank_account_id IS NOT NULL AND from_treasury_id IS NULL)),
    CONSTRAINT cash_transfers_to_chk CHECK (
        (to_kind = 'treasury' AND to_treasury_id IS NOT NULL AND to_bank_account_id IS NULL)
        OR (to_kind = 'bank' AND to_bank_account_id IS NOT NULL AND to_treasury_id IS NULL)),
    CONSTRAINT cash_transfers_same_chk CHECK (
        from_treasury_id IS DISTINCT FROM to_treasury_id OR from_bank_account_id IS DISTINCT FROM to_bank_account_id),
    CONSTRAINT cash_transfers_type_chk CHECK (transfer_type = CASE
        WHEN from_kind = 'treasury' AND to_kind = 'bank' THEN 'deposit'
        WHEN from_kind = 'bank' AND to_kind = 'treasury' THEN 'withdraw'
        WHEN from_kind = 'treasury' THEN 't2t' ELSE 'b2b' END),
    CONSTRAINT cash_transfers_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR transfer_no IS NOT NULL))
);
ALTER SEQUENCE cash.cash_transfers_draft_seq OWNED BY cash.cash_transfers.draft_no;
COMMENT ON TABLE cash.cash_transfers IS
    'حركات الخزائن والبنوك: من (خزنة / بنك) ← إلى (خزنة / بنك) + المبلغ + مصاريف بنكية · مسودة ← مرحّل بقيد أوتوماتيك';

-- القائمة بالفترة (الأحدث فوق) وبالنوع (التبويبات) + index على كل FK وعلى البحث.
CREATE INDEX cash_transfers_date_idx ON cash.cash_transfers (transfer_date DESC, id DESC);
CREATE INDEX cash_transfers_type_idx ON cash.cash_transfers (transfer_type, transfer_date DESC);
CREATE INDEX cash_transfers_year_idx ON cash.cash_transfers (fiscal_year_id, status);
CREATE INDEX cash_transfers_from_treasury_idx ON cash.cash_transfers (from_treasury_id) WHERE from_treasury_id IS NOT NULL;
CREATE INDEX cash_transfers_from_bank_idx ON cash.cash_transfers (from_bank_account_id) WHERE from_bank_account_id IS NOT NULL;
CREATE INDEX cash_transfers_to_treasury_idx ON cash.cash_transfers (to_treasury_id) WHERE to_treasury_id IS NOT NULL;
CREATE INDEX cash_transfers_to_bank_idx ON cash.cash_transfers (to_bank_account_id) WHERE to_bank_account_id IS NOT NULL;
CREATE INDEX cash_transfers_created_by_idx ON cash.cash_transfers (created_by);
CREATE INDEX cash_transfers_posted_by_idx ON cash.cash_transfers (posted_by);
CREATE INDEX cash_transfers_desc_idx ON cash.cash_transfers (lower(description) varchar_pattern_ops);
CREATE INDEX cash_transfers_ref_idx ON cash.cash_transfers (reference_no) WHERE reference_no IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3) الحماية
-- ---------------------------------------------------------------------------
-- طرف واحد من الحركة (خزنة / حساب بنك): موجود · نشط · بالعملة الأساسية.
CREATE FUNCTION cash.cash_transfer_endpoint_check(p_kind text, p_treasury integer, p_bank integer, p_side text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m     record;
    base  text;
BEGIN
    SELECT code INTO base FROM core.currencies WHERE is_base;
    IF p_kind = 'treasury' THEN
        SELECT code, name_ar, is_active, currency_code INTO m FROM cash.treasuries WHERE id = p_treasury FOR SHARE;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'الخزنة «% %» (%) موقوفة — اختار خزنة نشطة.', m.code, m.name_ar, p_side;
        END IF;
    ELSE
        SELECT ba.code, b.name_ar, ba.is_active AND b.is_active AS is_active, ba.currency_code INTO m
          FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id
         WHERE ba.id = p_bank FOR SHARE OF ba;
        IF NOT m.is_active THEN
            RAISE EXCEPTION 'الحساب البنكي «% %» (%) موقوف — اختار حساب نشط.', m.code, m.name_ar, p_side;
        END IF;
    END IF;
    IF m.currency_code IS DISTINCT FROM base THEN
        RAISE EXCEPTION '«% %» بعملة % — الحركة بالعملة الأساسية (%) بس.', m.code, m.name_ar, m.currency_code, base;
    END IF;
END $$;

CREATE FUNCTION cash.cash_transfers_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'الحركة ' || coalesce(OLD.transfer_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — حركاتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّلة — ماتتمسحش (الغي ترحيلها الأول).', lbl;
        END IF;
        IF OLD.transfer_no IS NOT NULL THEN
            RAISE EXCEPTION '% خدت رقم رسمي قبل كده — ماتتمسحش عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    -- النوع من «من» و«إلى» (دايماً — مايتكتبش من برّا).
    NEW.transfer_type := CASE
        WHEN NEW.from_kind = 'treasury' AND NEW.to_kind = 'bank' THEN 'deposit'
        WHEN NEW.from_kind = 'bank' AND NEW.to_kind = 'treasury' THEN 'withdraw'
        WHEN NEW.from_kind = 'treasury' THEN 't2t' ELSE 'b2b' END;

    -- السنة المالية من التاريخ (لازم مفتوحة).
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.transfer_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.transfer_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل فيها حركة.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.transfer_no IS NOT NULL THEN
            RAISE EXCEPTION 'الحركة الجديدة بتتسجل مسودة الأول (وبعدين تترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.transfer_no IS NOT NULL AND NEW.transfer_no IS DISTINCT FROM OLD.transfer_no) THEN
            RAISE EXCEPTION 'رقم الحركة ثابت.';
        END IF;
        IF OLD.transfer_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
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
            -- إلغاء الترحيل: الحالة بس (الحركة بترجع مسودة برقمها).
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
            IF NEW.transfer_no IS NULL THEN
                NEW.transfer_no := core.next_document_no('cash_transfer', NEW.transfer_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- «من» و«إلى»: وقت الإضافة أو التغيير أو الترحيل.
    IF TG_OP = 'INSERT' OR posting
       OR (NEW.from_treasury_id, NEW.from_bank_account_id) IS DISTINCT FROM (OLD.from_treasury_id, OLD.from_bank_account_id) THEN
        PERFORM cash.cash_transfer_endpoint_check(NEW.from_kind, NEW.from_treasury_id, NEW.from_bank_account_id, 'من');
    END IF;
    IF TG_OP = 'INSERT' OR posting
       OR (NEW.to_treasury_id, NEW.to_bank_account_id) IS DISTINCT FROM (OLD.to_treasury_id, OLD.to_bank_account_id) THEN
        PERFORM cash.cash_transfer_endpoint_check(NEW.to_kind, NEW.to_treasury_id, NEW.to_bank_account_id, 'إلى');
    END IF;
    RETURN NEW;
END $$;

-- المرحّلة لازم ليها قيد مرحّل بالمبلغ + المصاريف · المسودة مالهاش قيد (آخر الـ transaction).
CREATE FUNCTION cash.cash_transfers_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v  record;
    e  record;
BEGIN
    SELECT id, status, amount, fees, transfer_no, draft_no INTO v FROM cash.cash_transfers WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;                                     -- اتمسحت في نفس الـ transaction
    END IF;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'cash_transfer' AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'الحركة % مرحّلة من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', v.transfer_no;
        END IF;
        IF e.total_debit <> v.amount + v.fees THEN
            RAISE EXCEPTION 'قيد الحركة % مبلغه % مش زي المبلغ + المصاريف %.', v.transfer_no, e.total_debit,
                v.amount + v.fees;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION 'الحركة % مسودة وليها قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.',
            coalesce(v.transfer_no, 'مسودة ' || v.draft_no);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER cash_transfers_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.cash_transfers
    FOR EACH ROW EXECUTE FUNCTION cash.cash_transfers_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.cash_transfers
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER cash_transfers_entry_trg AFTER INSERT OR UPDATE ON cash.cash_transfers
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.cash_transfers_entry_check();

-- ---------------------------------------------------------------------------
-- 4) السنة المالية: نفس حماية السندات + الحركات (كل جدول بعمود تاريخه)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  text[];
    c  integer;
    n  integer := 0;
    docs constant text[][] := ARRAY[['cash.customer_receipts', 'voucher_date'], ['cash.supplier_payments', 'voucher_date'],
                                    ['cash.customer_payments', 'voucher_date'], ['cash.cash_transfers', 'transfer_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
