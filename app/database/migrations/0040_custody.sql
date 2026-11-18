-- 0040: «العهد» (المرحلة 5 — النموذج 1 «الكلاسيك» + تعديلات المستخدم 2026-11-18 — mockups/phase5/04_custody.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- ردود المستخدم (2026-11-18):
--   * النموذج 1: شاشة واحدة بزرار «صرف عهدة / تسوية عهدة» · مكانها قسم «الموظفين».
--   * **التسوية = طريقين**: بنود مصروفات (نوع المصروف · البيان · أمر التوريد اختياري · المبلغ) و/أو **فلوس راجعة**
--     لخزنة أو حساب بنك — كل سطر بيتضاف من **واجهة منبثقة** (مش جدول بيتكتب فيه).
--   * أول ما يختار الموظف ← **واجهة منبثقة بالعهد المفتوحة بس** بتشيك بوكس — يعلّم على عهدة أو أكتر يسوّيها.
--   * التسوية **أكبر** من العهد اللي اختارها (صرف من جيبه) ← بتتسجل والزيادة **«ليه»** (رصيده دائن في كشف حسابه)،
--     وتتصرف له من الخزنة (صرف بنوع «سداد اللي ليه») — أو من شاشة المصروفات بعدين (هيبقى فيها اختيار الموظف).
--   * «عهدة قيد التسوية» (بضاعة فاتورتها ماجتش) اتأجلت لمرحلة المشتريات · مفيش أعمار ولا كشف جوه الشاشة
--     (الكشف في التقارير) · مفيش نقل من الأكسس · مفيش طباعة دلوقتي.
-- اقتراحاتي اللي مااتردش عليها (زي النموذج): الصرف من خزنة أو حساب بنك · الرصيد مش مكفي ← تحذير بس · مسودة + ترحيل +
--   إلغاء الترحيل · الترقيم CD-2026-00001 للصرف و CS-2026-00001 للتسوية من أول ترحيل · رقم الإيصال اختياري ·
--   أمر التوريد اختياري على الصرف (معلومة) وعلى كل سطر مصروف (بيروح على القيد بمركز تكلفته) · العملة الأساسية بس.
--
-- القيود (بمحرك الترحيل 0032 — الحساب 1107 «عهد الموظفين» بمفتاح `custody` وبُعد الموظف، مفيش حساب جديد):
--   صرف عهدة / سداد اللي ليه:  مدين عهد الموظفين (الموظف) / دائن الخزنة أو البنك.
--   تسوية عهدة:  مدين حساب كل نوع مصروف (بأمر التوريد ومركز تكلفته) + مدين الخزنة / البنك بالمرتجع
--                / دائن عهد الموظفين (الموظف) بالإجمالي.
-- **ربط التسوية بالعهد** (`custody_settlement_links`): العهد اللي اتعلّم عليها، ووقت الترحيل الإجمالي بيتوزّع عليها
--   **الأقدم الأول** لحد الباقي من كل واحدة · اللي فاض = «ليه» (`excess_amount`) · المرتجع مايعدّيش الموزّع.
-- الحماية في القاعدة (زي السندات والحركات): السنة المفتوحة · المرحّل مايتعدّلش ولا يتمسح · الرقم ثابت · السطور والربط
--   بيتغيّروا في المسودة بس · Constraint Triggers مؤجلة: القيد = الإجمالي · التوزيع مايعدّيش العهدة · «سداد اللي ليه»
--   مايعدّيش اللي ليه · العهدة اللي اتسوّى منها مايتلغاش ترحيلها.

-- ---------------------------------------------------------------------------
-- 1) الترقيم + نوعين المستندات + قواعد الترحيل
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('custody_disb',   'صرف عهدة',   'CD', 5),
    ('custody_settle', 'تسوية عهدة', 'CS', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('custody_disb',   'صرف عهدة',   16),
    ('custody_settle', 'تسوية عهدة', 17);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('custody_disb',   'custody', 'عهد الموظفين',       'dr', 'party',    'custody', NULL, 'employee', 1),
    ('custody_disb',   'cash',    'الخزينة',            'cr', 'treasury', NULL,      NULL, NULL,       2),
    ('custody_disb',   'bank',    'البنك',              'cr', 'bank',     NULL,      NULL, NULL,       3),
    ('custody_settle', 'expense', 'المصروف',            'dr', 'expense',  NULL,      NULL, NULL,       1),
    ('custody_settle', 'cash',    'الخزينة (المرتجع)',  'dr', 'treasury', NULL,      NULL, NULL,       2),
    ('custody_settle', 'bank',    'البنك (المرتجع)',    'dr', 'bank',     NULL,      NULL, NULL,       3),
    ('custody_settle', 'custody', 'عهد الموظفين',       'cr', 'party',    'custody', NULL, 'employee', 4);

-- ---------------------------------------------------------------------------
-- 2) الجداول
-- ---------------------------------------------------------------------------
-- صرف العهدة (و«سداد اللي ليه» للموظف اللي صرف من جيبه — نفس القيد).
CREATE SEQUENCE cash.custody_disbursements_draft_seq AS integer;
CREATE TABLE cash.custody_disbursements (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disb_no          varchar(30),                                     -- CD-2026-00001 (من أول ترحيل)
    draft_no         integer        NOT NULL DEFAULT nextval('cash.custody_disbursements_draft_seq'),
    disb_date        date           NOT NULL,
    fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    employee_id      integer        NOT NULL REFERENCES party.employees (id) ON DELETE RESTRICT,
    purpose          varchar(10)    NOT NULL DEFAULT 'custody',       -- custody عهدة جديدة · payback سداد اللي ليه
    source_kind      varchar(10)    NOT NULL,                         -- treasury خزنة · bank حساب بنك
    treasury_id      integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    amount           numeric(18,2)  NOT NULL,
    project_id       integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,   -- أمر التوريد (معلومة — اختياري)
    description      varchar(500)   NOT NULL,
    reference_no     varchar(40),                                     -- رقم الإيصال الورقي (اختياري)
    status           varchar(10)    NOT NULL DEFAULT 'draft',
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at        timestamptz,
    posted_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT custody_disb_row_version_chk CHECK (row_version > 0),
    CONSTRAINT custody_disb_no_unique UNIQUE (disb_no),
    CONSTRAINT custody_disb_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT custody_disb_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT custody_disb_purpose_chk CHECK (purpose IN ('custody', 'payback')),
    CONSTRAINT custody_disb_amount_chk CHECK (amount > 0),
    CONSTRAINT custody_disb_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT custody_disb_ref_chk CHECK (reference_no IS NULL OR btrim(reference_no) <> ''),
    CONSTRAINT custody_disb_source_chk CHECK (
        (source_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
        OR (source_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL)),
    CONSTRAINT custody_disb_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR disb_no IS NOT NULL))
);
ALTER SEQUENCE cash.custody_disbursements_draft_seq OWNED BY cash.custody_disbursements.draft_no;
COMMENT ON TABLE cash.custody_disbursements IS
    'صرف عهدة لموظف (أو سداد اللي ليه لما يصرف من جيبه) من خزنة / بنك · مسودة ← مرحّل بقيد أوتوماتيك';

CREATE INDEX custody_disb_date_idx ON cash.custody_disbursements (disb_date DESC, id DESC);
CREATE INDEX custody_disb_employee_idx ON cash.custody_disbursements (employee_id, status, disb_date);
CREATE INDEX custody_disb_year_idx ON cash.custody_disbursements (fiscal_year_id, status);
CREATE INDEX custody_disb_treasury_idx ON cash.custody_disbursements (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX custody_disb_bank_idx ON cash.custody_disbursements (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX custody_disb_project_idx ON cash.custody_disbursements (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX custody_disb_created_by_idx ON cash.custody_disbursements (created_by);
CREATE INDEX custody_disb_posted_by_idx ON cash.custody_disbursements (posted_by);
CREATE INDEX custody_disb_desc_idx ON cash.custody_disbursements (lower(description) varchar_pattern_ops);

-- تسوية العهدة (الإجماليات بتتحسب من السطور — والموزّع / «ليه» بيتحسبوا وقت الترحيل).
CREATE SEQUENCE cash.custody_settlements_draft_seq AS integer;
CREATE TABLE cash.custody_settlements (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    settle_no        varchar(30),                                     -- CS-2026-00001 (من أول ترحيل)
    draft_no         integer        NOT NULL DEFAULT nextval('cash.custody_settlements_draft_seq'),
    settle_date      date           NOT NULL,
    fiscal_year_id   integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    employee_id      integer        NOT NULL REFERENCES party.employees (id) ON DELETE RESTRICT,
    description      varchar(500)   NOT NULL,
    reference_no     varchar(40),
    expense_total    numeric(18,2)  NOT NULL DEFAULT 0,              -- مجموع بنود المصروفات
    return_total     numeric(18,2)  NOT NULL DEFAULT 0,              -- مجموع الفلوس الراجعة
    allocated_total  numeric(18,2)  NOT NULL DEFAULT 0,              -- اللي اتوزّع على العهد (وقت الترحيل)
    excess_amount    numeric(18,2)  NOT NULL DEFAULT 0,              -- الزيادة = «ليه» (وقت الترحيل)
    status           varchar(10)    NOT NULL DEFAULT 'draft',
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at        timestamptz,
    posted_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    updated_at       timestamptz    NOT NULL DEFAULT now(),
    row_version      integer        NOT NULL DEFAULT 1 CONSTRAINT custody_settle_row_version_chk CHECK (row_version > 0),
    CONSTRAINT custody_settle_no_unique UNIQUE (settle_no),
    CONSTRAINT custody_settle_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT custody_settle_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT custody_settle_totals_chk CHECK (expense_total >= 0 AND return_total >= 0 AND allocated_total >= 0
                                                AND excess_amount >= 0),
    CONSTRAINT custody_settle_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT custody_settle_ref_chk CHECK (reference_no IS NULL OR btrim(reference_no) <> ''),
    CONSTRAINT custody_settle_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR settle_no IS NOT NULL))
);
ALTER SEQUENCE cash.custody_settlements_draft_seq OWNED BY cash.custody_settlements.draft_no;
COMMENT ON TABLE cash.custody_settlements IS
    'تسوية عهدة: بنود مصروفات + فلوس راجعة لخزنة / بنك · بتتوزّع على العهد المختارة الأقدم الأول والزيادة «ليه»';

CREATE INDEX custody_settle_date_idx ON cash.custody_settlements (settle_date DESC, id DESC);
CREATE INDEX custody_settle_employee_idx ON cash.custody_settlements (employee_id, status, settle_date);
CREATE INDEX custody_settle_year_idx ON cash.custody_settlements (fiscal_year_id, status);
CREATE INDEX custody_settle_created_by_idx ON cash.custody_settlements (created_by);
CREATE INDEX custody_settle_posted_by_idx ON cash.custody_settlements (posted_by);
CREATE INDEX custody_settle_desc_idx ON cash.custody_settlements (lower(description) varchar_pattern_ops);

-- سطور التسوية: مصروف (نوع + أمر توريد) أو مرتجع (خزنة / بنك).
CREATE TABLE cash.custody_settlement_lines (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    settlement_id    integer        NOT NULL REFERENCES cash.custody_settlements (id) ON DELETE CASCADE,
    line_no          smallint       NOT NULL,
    line_kind        varchar(10)    NOT NULL,                         -- expense مصروف · return مرتجع
    expense_type_id  integer        REFERENCES exp.expense_types (id) ON DELETE RESTRICT,
    project_id       integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,
    return_kind      varchar(10),                                     -- treasury · bank (للمرتجع)
    treasury_id      integer        REFERENCES cash.treasuries (id) ON DELETE RESTRICT,
    bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,
    amount           numeric(18,2)  NOT NULL,
    description      varchar(300),
    CONSTRAINT custody_lines_no_unique UNIQUE (settlement_id, line_no),
    CONSTRAINT custody_lines_no_chk CHECK (line_no > 0),
    CONSTRAINT custody_lines_amount_chk CHECK (amount > 0),
    CONSTRAINT custody_lines_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT custody_lines_kind_chk CHECK (
        (line_kind = 'expense' AND expense_type_id IS NOT NULL AND return_kind IS NULL
             AND treasury_id IS NULL AND bank_account_id IS NULL)
        OR (line_kind = 'return' AND expense_type_id IS NULL AND project_id IS NULL AND (
             (return_kind = 'treasury' AND treasury_id IS NOT NULL AND bank_account_id IS NULL)
             OR (return_kind = 'bank' AND bank_account_id IS NOT NULL AND treasury_id IS NULL))))
);
CREATE INDEX custody_lines_expense_type_idx ON cash.custody_settlement_lines (expense_type_id) WHERE expense_type_id IS NOT NULL;
CREATE INDEX custody_lines_project_idx ON cash.custody_settlement_lines (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX custody_lines_treasury_idx ON cash.custody_settlement_lines (treasury_id) WHERE treasury_id IS NOT NULL;
CREATE INDEX custody_lines_bank_idx ON cash.custody_settlement_lines (bank_account_id) WHERE bank_account_id IS NOT NULL;
COMMENT ON TABLE cash.custody_settlement_lines IS 'سطور تسوية العهدة: بند مصروف (نوع · أمر توريد) أو فلوس راجعة (خزنة / بنك)';

-- العهد اللي اتعلّم عليها في التسوية + الموزّع على كل واحدة (صفر في المسودة).
CREATE TABLE cash.custody_settlement_links (
    settlement_id     integer        NOT NULL REFERENCES cash.custody_settlements (id) ON DELETE CASCADE,
    disbursement_id   integer        NOT NULL REFERENCES cash.custody_disbursements (id) ON DELETE RESTRICT,
    allocated_amount  numeric(18,2)  NOT NULL DEFAULT 0,
    PRIMARY KEY (settlement_id, disbursement_id),
    CONSTRAINT custody_links_amount_chk CHECK (allocated_amount >= 0)
);
CREATE INDEX custody_links_disbursement_idx ON cash.custody_settlement_links (disbursement_id);
COMMENT ON TABLE cash.custody_settlement_links IS 'العهد اللي التسوية بتقفلها + المبلغ اللي اتوزّع على كل واحدة (وقت الترحيل)';

-- ---------------------------------------------------------------------------
-- 3) الحماية: الموظف والخزنة / البنك
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.custody_employee_check(p_employee integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    m  record;
BEGIN
    SELECT code, name_ar, is_active, takes_custody INTO m FROM party.employees WHERE id = p_employee FOR SHARE;
    IF NOT m.is_active THEN
        RAISE EXCEPTION 'الموظف «% %» موقوف — نشّطه الأول من شاشة الموظفين.', m.code, m.name_ar;
    END IF;
    IF NOT m.takes_custody THEN
        RAISE EXCEPTION 'الموظف «% %» مش عليه دور «عهدة» — علّم عليه من شاشة الموظفين.', m.code, m.name_ar;
    END IF;
END $$;

-- اللي ليه عند الموظف (الزيادات المرحّلة − اللي اتسددله) مايبقاش بالسالب.
CREATE FUNCTION cash.custody_credit_check(p_employee integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_excess   numeric;
    v_payback  numeric;
BEGIN
    SELECT coalesce(sum(excess_amount), 0) INTO v_excess
      FROM cash.custody_settlements WHERE employee_id = p_employee AND status = 'posted';
    SELECT coalesce(sum(amount), 0) INTO v_payback
      FROM cash.custody_disbursements WHERE employee_id = p_employee AND status = 'posted' AND purpose = 'payback';
    IF v_payback > v_excess THEN
        RAISE EXCEPTION 'اللي اتسدد للموظف (%) أكبر من اللي ليه من التسويات (%) — الغي ترحيل «سداد اللي ليه» الأول.',
            v_payback, v_excess;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 4) حماية صرف العهدة
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.custody_disbursements_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
    other    text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'صرف العهدة ' || coalesce(OLD.disb_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — مستنداتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.disb_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        SELECT coalesce(s.settle_no, 'مسودة ' || s.draft_no) INTO other
          FROM cash.custody_settlement_links l JOIN cash.custody_settlements s ON s.id = l.settlement_id
         WHERE l.disbursement_id = OLD.id LIMIT 1;
        IF other IS NOT NULL THEN
            RAISE EXCEPTION '% متعلّم عليه في التسوية % — شيله منها الأول.', lbl, other;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.disb_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.disb_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها صرف عهدة.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;
    IF NEW.purpose = 'payback' THEN
        NEW.project_id := NULL;                          -- السداد مالوش أمر توريد
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.disb_no IS NOT NULL THEN
            RAISE EXCEPTION 'صرف العهدة الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.disb_no IS NOT NULL AND NEW.disb_no IS DISTINCT FROM OLD.disb_no) THEN
            RAISE EXCEPTION 'رقم صرف العهدة ثابت.';
        END IF;
        IF OLD.disb_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمه من سنة % — تاريخه لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF (NEW.employee_id <> OLD.employee_id OR NEW.purpose <> OLD.purpose)
           AND EXISTS (SELECT 1 FROM cash.custody_settlement_links WHERE disbursement_id = OLD.id) THEN
            RAISE EXCEPTION '% متعلّم عليه في تسوية — الموظف والنوع مايتغيّروش.', lbl;
        END IF;
        IF OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION '% مرحّل — مايتعدّلش (الغي ترحيله الأول).', lbl;
                END IF;
                RETURN NEW;
            END IF;
            -- إلغاء الترحيل: الحالة بس — وممنوع لو اتسوّى منه.
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع صرف العهدة مسودة بس — التعديل بعد كده.';
            END IF;
            SELECT s.settle_no INTO other
              FROM cash.custody_settlement_links l JOIN cash.custody_settlements s ON s.id = l.settlement_id
             WHERE l.disbursement_id = OLD.id AND s.status = 'posted' LIMIT 1;
            IF other IS NOT NULL THEN
                RAISE EXCEPTION '% اتسوّى منه بالتسوية % — الغي ترحيل التسوية الأول.', lbl, other;
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NEW.disb_no IS NULL THEN
                NEW.disb_no := core.next_document_no('custody_disb', NEW.disb_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    IF TG_OP = 'INSERT' OR posting OR NEW.employee_id <> OLD.employee_id THEN
        PERFORM cash.custody_employee_check(NEW.employee_id);
    END IF;
    IF TG_OP = 'INSERT' OR posting
       OR (NEW.treasury_id, NEW.bank_account_id) IS DISTINCT FROM (OLD.treasury_id, OLD.bank_account_id) THEN
        PERFORM cash.cash_transfer_endpoint_check(NEW.source_kind, NEW.treasury_id, NEW.bank_account_id, 'الصرف من');
    END IF;
    RETURN NEW;
END $$;

-- المرحّل لازم ليه قيد مرحّل بالمبلغ والمسودة مالهاش قيد · «سداد اللي ليه» مايعدّيش اللي ليه.
CREATE FUNCTION cash.custody_disbursements_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v  record;
    e  record;
BEGIN
    SELECT id, status, amount, disb_no, draft_no, employee_id, purpose INTO v
      FROM cash.custody_disbursements WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'custody_disb' AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'صرف العهدة % مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', v.disb_no;
        END IF;
        IF e.total_debit <> v.amount THEN
            RAISE EXCEPTION 'قيد صرف العهدة % مبلغه % مش زي المبلغ %.', v.disb_no, e.total_debit, v.amount;
        END IF;
    ELSIF e.status IS NOT NULL THEN
        RAISE EXCEPTION 'صرف العهدة % مسودة وليه قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.',
            coalesce(v.disb_no, 'مسودة ' || v.draft_no);
    END IF;
    IF v.purpose = 'payback' THEN
        PERFORM cash.custody_credit_check(v.employee_id);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER custody_disb_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.custody_disbursements
    FOR EACH ROW EXECUTE FUNCTION cash.custody_disbursements_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.custody_disbursements
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER custody_disb_entry_trg AFTER INSERT OR UPDATE ON cash.custody_disbursements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.custody_disbursements_entry_check();

-- ---------------------------------------------------------------------------
-- 5) حماية التسوية
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.custody_settlements_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'التسوية ' || coalesce(OLD.settle_no, 'مسودة ' || OLD.draft_no);
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — مستنداتها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّلة — ماتتمسحش (الغي ترحيلها الأول).', lbl;
        END IF;
        IF OLD.settle_no IS NOT NULL THEN
            RAISE EXCEPTION '% خدت رقم رسمي قبل كده — ماتتمسحش عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE NEW.settle_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.settle_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل فيها تسوية.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;
    IF NEW.status = 'draft' THEN
        NEW.allocated_total := 0;                         -- التوزيع وقت الترحيل بس
        NEW.excess_amount := 0;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.settle_no IS NOT NULL THEN
            RAISE EXCEPTION 'التسوية الجديدة بتتسجل مسودة الأول (وبعدين تترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.settle_no IS NOT NULL AND NEW.settle_no IS DISTINCT FROM OLD.settle_no) THEN
            RAISE EXCEPTION 'رقم التسوية ثابت.';
        END IF;
        IF OLD.settle_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمها من سنة % — تاريخها لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF NEW.employee_id <> OLD.employee_id
           AND EXISTS (SELECT 1 FROM cash.custody_settlement_links WHERE settlement_id = OLD.id) THEN
            RAISE EXCEPTION '% عليها عهد متعلّم عليها — شيلها الأول قبل ما تغيّر الموظف.', lbl;
        END IF;
        IF OLD.status = 'posted' THEN
            IF NEW.status = 'posted' THEN
                IF (to_jsonb(NEW) - 'row_version' - 'updated_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION '% مرحّلة — ماتتعدّلش (الغي ترحيلها الأول).', lbl;
                END IF;
                RETURN NEW;
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'allocated_total' - 'excess_amount'
                    - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'allocated_total' - 'excess_amount'
                    - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع التسوية مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NEW.settle_no IS NULL THEN
                NEW.settle_no := core.next_document_no('custody_settle', NEW.settle_date);
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    IF TG_OP = 'INSERT' OR posting OR NEW.employee_id <> OLD.employee_id THEN
        PERFORM cash.custody_employee_check(NEW.employee_id);
    END IF;
    RETURN NEW;
END $$;

-- السطور والربط بيتغيّروا والتسوية مسودة بس (والربط على عهدة مصروفة لنفس الموظف).
CREATE FUNCTION cash.custody_settlement_children_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    s   record;
    d   record;
    sid integer;
BEGIN
    sid := CASE WHEN TG_OP = 'DELETE' THEN OLD.settlement_id ELSE NEW.settlement_id END;
    IF TG_OP = 'UPDATE' AND NEW.settlement_id <> OLD.settlement_id THEN
        RAISE EXCEPTION 'السطر مايتنقلش من تسوية لتسوية.';
    END IF;
    SELECT id, status, employee_id, coalesce(settle_no, 'مسودة ' || draft_no) AS lbl INTO s
      FROM cash.custody_settlements WHERE id = sid;
    IF s.id IS NULL THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- التسوية نفسها بتتمسح
    END IF;
    IF s.status <> 'draft' THEN
        RAISE EXCEPTION 'التسوية % مرحّلة — سطورها وعهدها ماتتغيّرش (الغي ترحيلها الأول).', s.lbl;
    END IF;
    IF TG_TABLE_NAME = 'custody_settlement_links' AND TG_OP <> 'DELETE' THEN
        IF TG_OP = 'UPDATE' AND NEW.disbursement_id <> OLD.disbursement_id THEN
            RAISE EXCEPTION 'العهدة في الربط ماتتغيّرش — امسح الربط وضيف واحد جديد.';
        END IF;
        SELECT employee_id, purpose, coalesce(disb_no, 'مسودة ' || draft_no) AS lbl INTO d
          FROM cash.custody_disbursements WHERE id = NEW.disbursement_id;
        IF d.employee_id <> s.employee_id THEN
            RAISE EXCEPTION 'العهدة % مش بتاعة نفس الموظف بتاع التسوية.', d.lbl;
        END IF;
        IF d.purpose <> 'custody' THEN
            RAISE EXCEPTION '% سداد للموظف مش عهدة — ماتتسوّاش.', d.lbl;
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

-- آخر الـ transaction: الإجماليات = السطور · المرحّلة ليها قيد بالإجمالي · الموزّع + «ليه» = الإجمالي ·
-- المرتجع مايعدّيش الموزّع · العهد مرحّلة ومااتوزّعش عليها أكتر من مبلغها · المسودة من غير قيد ولا توزيع.
CREATE FUNCTION cash.custody_settlement_check(p_id integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    t      record;
    d      record;
    alloc  numeric;
BEGIN
    SELECT id, status, settle_no, draft_no, employee_id, expense_total, return_total, allocated_total, excess_amount
      INTO v FROM cash.custody_settlements WHERE id = p_id;
    IF v.id IS NULL THEN
        RETURN;
    END IF;
    SELECT coalesce(sum(amount) FILTER (WHERE line_kind = 'expense'), 0) AS ex,
           coalesce(sum(amount) FILTER (WHERE line_kind = 'return'), 0) AS rt INTO t
      FROM cash.custody_settlement_lines WHERE settlement_id = p_id;
    IF t.ex <> v.expense_total OR t.rt <> v.return_total THEN
        RAISE EXCEPTION 'إجماليات التسوية % مش زي سطورها (مصروفات % · مرتجع %).',
            coalesce(v.settle_no, 'مسودة ' || v.draft_no), t.ex, t.rt;
    END IF;
    SELECT coalesce(sum(allocated_amount), 0) INTO alloc FROM cash.custody_settlement_links WHERE settlement_id = p_id;
    SELECT status, total_debit INTO e FROM acc.journal_entries WHERE source_type = 'custody_settle' AND source_id = p_id;
    IF v.status = 'posted' THEN
        IF v.expense_total + v.return_total <= 0 THEN
            RAISE EXCEPTION 'التسوية % من غير سطور.', v.settle_no;
        END IF;
        IF e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'التسوية % مرحّلة من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', v.settle_no;
        END IF;
        IF e.total_debit <> v.expense_total + v.return_total THEN
            RAISE EXCEPTION 'قيد التسوية % مبلغه % مش زي إجماليها %.', v.settle_no, e.total_debit,
                v.expense_total + v.return_total;
        END IF;
        IF alloc <> v.allocated_total OR v.allocated_total + v.excess_amount <> v.expense_total + v.return_total THEN
            RAISE EXCEPTION 'توزيع التسوية % على العهد مش مظبوط (الموزّع % + ليه % ≠ الإجمالي %).', v.settle_no,
                alloc, v.excess_amount, v.expense_total + v.return_total;
        END IF;
        IF v.return_total > v.allocated_total THEN
            RAISE EXCEPTION 'التسوية %: الفلوس الراجعة (%) أكبر من الباقي في العهد اللي اتعلّم عليها (%).',
                v.settle_no, v.return_total, v.allocated_total;
        END IF;
        FOR d IN
            SELECT c.id, c.amount, c.status, coalesce(c.disb_no, 'مسودة ' || c.draft_no) AS lbl,
                   (SELECT coalesce(sum(x.allocated_amount), 0) FROM cash.custody_settlement_links x
                     WHERE x.disbursement_id = c.id) AS used
              FROM cash.custody_settlement_links l JOIN cash.custody_disbursements c ON c.id = l.disbursement_id
             WHERE l.settlement_id = p_id
        LOOP
            IF d.status <> 'posted' THEN
                RAISE EXCEPTION 'العهدة % مش مرحّلة — مينفعش تتسوّى.', d.lbl;
            END IF;
            IF d.used > d.amount THEN
                RAISE EXCEPTION 'العهدة % اتسوّى منها % وهي % بس — اتسوّت من جهاز تاني؟', d.lbl, d.used, d.amount;
            END IF;
        END LOOP;
    ELSE
        IF e.status IS NOT NULL THEN
            RAISE EXCEPTION 'التسوية % مسودة وليها قيد — إلغاء الترحيل لازم يمسح القيد في نفس العملية.',
                coalesce(v.settle_no, 'مسودة ' || v.draft_no);
        END IF;
        IF alloc <> 0 THEN
            RAISE EXCEPTION 'التسوية % مسودة وعليها توزيع على العهد — التوزيع وقت الترحيل بس.',
                coalesce(v.settle_no, 'مسودة ' || v.draft_no);
        END IF;
    END IF;
    PERFORM cash.custody_credit_check(v.employee_id);
END $$;

CREATE FUNCTION cash.custody_settlements_entry_check() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cash.custody_settlement_check(NEW.id);
    RETURN NULL;
END $$;

CREATE FUNCTION cash.custody_settlement_children_check() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cash.custody_settlement_check(CASE WHEN TG_OP = 'DELETE' THEN OLD.settlement_id ELSE NEW.settlement_id END);
    RETURN NULL;
END $$;

CREATE TRIGGER custody_settle_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.custody_settlements
    FOR EACH ROW EXECUTE FUNCTION cash.custody_settlements_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.custody_settlements
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER custody_settle_entry_trg AFTER INSERT OR UPDATE ON cash.custody_settlements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.custody_settlements_entry_check();

CREATE TRIGGER custody_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.custody_settlement_lines
    FOR EACH ROW EXECUTE FUNCTION cash.custody_settlement_children_guard();
CREATE CONSTRAINT TRIGGER custody_lines_check_trg AFTER INSERT OR UPDATE OR DELETE ON cash.custody_settlement_lines
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.custody_settlement_children_check();
CREATE TRIGGER custody_links_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.custody_settlement_links
    FOR EACH ROW EXECUTE FUNCTION cash.custody_settlement_children_guard();
CREATE CONSTRAINT TRIGGER custody_links_check_trg AFTER INSERT OR UPDATE OR DELETE ON cash.custody_settlement_links
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.custody_settlement_children_check();

-- ---------------------------------------------------------------------------
-- 6) السنة المالية: نفس حماية السندات والحركات والشيكات + العهد
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
                                    ['cash.custody_settlements', 'settle_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
