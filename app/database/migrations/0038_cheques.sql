-- 0038: «شيكات العملاء» و«شيكات الموردين» (المرحلة 5 — النموذج 1 «الكلاسيك: القائمة + الشيك المحدد وخطواته»
--       + كارت تنبيهات الاستحقاق فوق، اختيار المستخدم 2026-11-17 — mockups/phase5/03_cheques.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (ردود 2026-11-17):
--   * **شاشتين** «شيكات العملاء» و«شيكات الموردين» زي الأكسس — والاتنين جدول واحد من جوه (`direction` in / out).
--   * حساب جديد **«شيكات تحت التحصيل»** (1108 — عشان الميزانية تفرّق اللي في الحافظة عن اللي في البنك مستني).
--   * الدورة والقيود زي جدول النماذج بالظبط (موافق — «سيبها زي ما هي»):
--       وارد:  استلام      مدين أوراق القبض        / دائن العملاء
--              إيداع       مدين شيكات تحت التحصيل  / دائن أوراق القبض   (ولو كان راجع من البنك: دائن العملاء)
--              تحصيل       مدين البنك (+ مصاريف بنكية) / دائن شيكات تحت التحصيل
--              رجع من البنك مدين العملاء           / دائن شيكات تحت التحصيل   (+ مصاريف بنكية اختياري على البنك)
--              تظهير لمورد مدين الموردين           / دائن أوراق القبض
--              رد للعميل   مدين العملاء            / دائن أوراق القبض   (ولو كان راجع من البنك: من غير قيد)
--       صادر:  إصدار       مدين الموردين           / دائن أوراق الدفع
--              صرف         مدين أوراق الدفع        / دائن البنك
--              رجع/اتلغى   مدين أوراق الدفع        / دائن الموردين
--   * الشيك اللي رجع من البنك ← على العميل تاني، ومنه «إيداع تاني» أو «تم الرد للعميل».
--   * «حفظ مسودة» و«ترحيل» (= استلام / إصدار) · وكل خطوة بعد كده قيد مرحّل بتاريخها · «تراجع عن آخر خطوة» بالصلاحية.
--   * الترقيم **CHR-2026-00001** للوارد و**CHP-2026-00001** للصادر (كل سنة من 1) · رقم الشيك مطلوب ومايتكررش على
--     نفس البنك.
--   * بنك الساحب (الوارد) **من قايمة البنوك بتاعتنا** (cash.banks) · بنكنا اللي اتودع فيه بيتحدد وقت الإيداع ·
--     الصادر من حساب بنكي بتاعنا.
--   * أمر التوريد اختياري · مفيش حافظة إيداع · مفيش نقل من الأكسس · الطباعة والتقارير بعدين.
-- الحماية في القاعدة نفسها:
--   * الشيك المسودة بس هو اللي بيتعدّل ويتمسح (لو ماخدش رقم) · بعد الترحيل البيانات ثابتة والحالة بتتغير بالخطوات بس.
--   * كل خطوة = صف في `cash.cheque_events` (مايتعدّلش — آخر صف بس هو اللي يتمسح = «تراجع») وليها قيد مرحّل بتاريخها
--     ومبلغها (Constraint Trigger مؤجل) · حالة الشيك = حالة آخر خطوة دايماً.
--   * الانتقالات المسموحة بس (من غير «قفز») · تاريخ الخطوة مش قبل الخطوة اللي قبلها وفي سنة مفتوحة.
--   * السنة ماتتقفلش وفيها شيكات مسودة، وتواريخها ماتسيبش شيك برّاها.

-- ---------------------------------------------------------------------------
-- 1) حساب «شيكات تحت التحصيل» + دوره + مفتاحه في الحسابات الافتراضية
-- ---------------------------------------------------------------------------
INSERT INTO acc.account_roles (code, name_ar, name_en, account_type, default_party_kind, sort_order)
VALUES ('notes_coll', 'شيكات تحت التحصيل', 'Cheques Under Collection', 'asset', 'customer',
        (SELECT max(sort_order) + 1 FROM acc.account_roles));

-- الحساب جنب «أوراق القبض» (نفس الأب) — 1108 لو فاضي، وإلا أول كود فاضي تحت نفس الأب.
DO $$
DECLARE
    p       acc.accounts%ROWTYPE;
    v_code  text;
    n       integer;
BEGIN
    IF EXISTS (SELECT 1 FROM acc.accounts WHERE role_code = 'notes_coll') THEN
        RETURN;
    END IF;
    SELECT par.* INTO p FROM acc.accounts a JOIN acc.accounts par ON par.id = a.parent_id
     WHERE a.role_code = 'notes_rec' ORDER BY length(a.code), a.code LIMIT 1;
    IF NOT FOUND THEN
        SELECT * INTO p FROM acc.accounts WHERE code = '11' AND NOT is_leaf;
    END IF;
    IF p.id IS NULL OR p.is_leaf OR NOT p.is_active OR p.level >= 5 THEN
        RETURN;                                      -- الدليل متغيّر خالص — المستخدم يحدد الحساب من «الحسابات الافتراضية»
    END IF;
    v_code := p.code || '08';
    IF EXISTS (SELECT 1 FROM acc.accounts WHERE code = v_code) THEN
        v_code := NULL;
        FOR n IN 1..99 LOOP
            IF NOT EXISTS (SELECT 1 FROM acc.accounts WHERE code = p.code || lpad(n::text, 2, '0')) THEN
                v_code := p.code || lpad(n::text, 2, '0');
                EXIT;
            END IF;
        END LOOP;
    END IF;
    IF v_code IS NULL THEN
        RETURN;
    END IF;
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, party_kind, is_system)
    VALUES (v_code, 'شيكات تحت التحصيل', 'Cheques Under Collection', p.id, true, 'asset', 'dr', 'current',
            'op', 'notes_coll', 'customer', true);
END $$;

-- المفتاح بعد «أوراق القبض» على طول (الترتيب بس — من غير ما يبان تعديل من حد).
ALTER TABLE acc.default_accounts DISABLE TRIGGER zz_row_version_trg;
UPDATE acc.default_accounts SET sort_order = sort_order + 1 WHERE sort_order >= 16;
INSERT INTO acc.default_accounts (key, group_code, name_ar, account_type, role_code, used_in, sort_order, account_id)
VALUES ('cheques_coll', 'cash', 'شيكات تحت التحصيل', 'asset', 'notes_coll', 'إيداع شيك للتحصيل', 16,
        (SELECT id FROM acc.accounts WHERE role_code = 'notes_coll' AND is_leaf AND is_active ORDER BY code LIMIT 1));
ALTER TABLE acc.default_accounts ENABLE TRIGGER zz_row_version_trg;

-- ---------------------------------------------------------------------------
-- 2) الترقيم + أنواع المستندات + قواعد الترحيل (القيد لكل خطوة: source_id = id الخطوة)
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('cheque_in',  'شيك وارد (عملاء)',  'CHR', 5),
    ('cheque_out', 'شيك صادر (موردين)', 'CHP', 5);

INSERT INTO acc.posting_doc_types (code, name_ar, sort_order) VALUES
    ('cheque_in',  'شيكات العملاء',  14),
    ('cheque_out', 'شيكات الموردين', 15);

INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, default_key, category_kind, party_kind, sort_order) VALUES
    ('cheque_in',  'notes_dr', 'أوراق القبض',        'dr', 'default', 'notes_rec',    NULL, 'customer', 1),
    ('cheque_in',  'coll_dr',  'شيكات تحت التحصيل',  'dr', 'default', 'cheques_coll', NULL, 'customer', 2),
    ('cheque_in',  'bank_dr',  'البنك',              'dr', 'bank',    NULL,           NULL, NULL,       3),
    ('cheque_in',  'fees',     'مصاريف بنكية',       'dr', 'default', 'bank_charges', NULL, NULL,       4),
    ('cheque_in',  'cust_dr',  'العملاء',            'dr', 'party',   NULL,           NULL, 'customer', 5),
    ('cheque_in',  'supp_dr',  'الموردين (تظهير)',   'dr', 'party',   NULL,           NULL, 'supplier', 6),
    ('cheque_in',  'cust_cr',  'العملاء',            'cr', 'party',   NULL,           NULL, 'customer', 7),
    ('cheque_in',  'notes_cr', 'أوراق القبض',        'cr', 'default', 'notes_rec',    NULL, 'customer', 8),
    ('cheque_in',  'coll_cr',  'شيكات تحت التحصيل',  'cr', 'default', 'cheques_coll', NULL, 'customer', 9),
    ('cheque_in',  'bank_cr',  'البنك',              'cr', 'bank',    NULL,           NULL, NULL,       10),
    ('cheque_out', 'supp_dr',  'الموردين',           'dr', 'party',   NULL,           NULL, 'supplier', 1),
    ('cheque_out', 'notes_dr', 'أوراق الدفع',        'dr', 'default', 'notes_pay',    NULL, 'supplier', 2),
    ('cheque_out', 'notes_cr', 'أوراق الدفع',        'cr', 'default', 'notes_pay',    NULL, 'supplier', 3),
    ('cheque_out', 'supp_cr',  'الموردين',           'cr', 'party',   NULL,           NULL, 'supplier', 4),
    ('cheque_out', 'bank_cr',  'البنك',              'cr', 'bank',    NULL,           NULL, NULL,       5);

-- ---------------------------------------------------------------------------
-- 3) الانتقالات المسموحة (الخطوة ← من حالة ← لحالة) — مصدر واحد للقاعدة والبرنامج
-- ---------------------------------------------------------------------------
CREATE TABLE cash.cheque_transitions (
    direction    varchar(3)   NOT NULL,
    event_type   varchar(15)  NOT NULL,
    from_status  varchar(20)  NOT NULL,
    to_status    varchar(20)  NOT NULL,
    has_entry    boolean      NOT NULL DEFAULT true,
    name_ar      varchar(40)  NOT NULL,
    sort_order   smallint     NOT NULL,
    PRIMARY KEY (direction, event_type, from_status)
);
COMMENT ON TABLE cash.cheque_transitions IS 'دورة الشيك: كل خطوة مسموحة من حالة لحالة (ولو ليها قيد) — ثابتة من البرنامج';

INSERT INTO cash.cheque_transitions (direction, event_type, from_status, to_status, has_entry, name_ar, sort_order) VALUES
    ('in',  'receive',  'draft',            'portfolio',        true,  'استلام',            1),
    ('in',  'deposit',  'portfolio',        'under_collection', true,  'إيداع للتحصيل',     2),
    ('in',  'deposit',  'bounced',          'under_collection', true,  'إيداع تاني',        3),
    ('in',  'collect',  'under_collection', 'collected',        true,  'تم التحصيل',        4),
    ('in',  'bounce',   'under_collection', 'bounced',          true,  'رجع من البنك',      5),
    ('in',  'endorse',  'portfolio',        'endorsed',         true,  'تظهير لمورد',       6),
    ('in',  'return',   'portfolio',        'returned',         true,  'رد للعميل',         7),
    ('in',  'return',   'bounced',          'returned',         false, 'رد للعميل',         8),
    ('out', 'issue',    'draft',            'issued',           true,  'إصدار',             1),
    ('out', 'clear',    'issued',           'cleared',          true,  'تم الصرف',          2),
    ('out', 'cancel',   'issued',           'cancelled',        true,  'رجع / اتلغى',       3);

-- ---------------------------------------------------------------------------
-- 4) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE cash.cheques_draft_seq AS integer;
CREATE TABLE cash.cheques (
    id                integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    direction         varchar(3)     NOT NULL,                       -- in وارد (عميل) · out صادر (مورد)
    cheque_ref        varchar(30),                                   -- CHR / CHP-2026-00001 (من أول ترحيل)
    draft_no          integer        NOT NULL DEFAULT nextval('cash.cheques_draft_seq'),
    cheque_date       date           NOT NULL,                       -- تاريخ الاستلام / الإصدار
    due_date          date           NOT NULL,                       -- تاريخ الاستحقاق
    fiscal_year_id    integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    cheque_no         varchar(30)    NOT NULL,                       -- رقم الشيك زي ما هو على الورقة
    customer_id       integer        REFERENCES party.customers (id) ON DELETE RESTRICT,   -- الوارد
    supplier_id       integer        REFERENCES party.suppliers (id) ON DELETE RESTRICT,   -- الصادر · أو المظهَّر له
    drawer_bank_id    integer        REFERENCES cash.banks (id) ON DELETE RESTRICT,        -- بنك الساحب (الوارد)
    bank_account_id   integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT, -- حسابنا: الصادر منه · الوارد اتودع فيه
    amount            numeric(18,2)  NOT NULL,
    description       varchar(500)   NOT NULL,
    project_id        integer        REFERENCES acc.projects (id) ON DELETE RESTRICT,     -- أمر التوريد (اختياري)
    status            varchar(20)    NOT NULL DEFAULT 'draft',
    status_date       date,                                          -- تاريخ آخر خطوة
    created_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at         timestamptz,
    posted_by         integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at        timestamptz    NOT NULL DEFAULT now(),
    updated_at        timestamptz    NOT NULL DEFAULT now(),
    row_version       integer        NOT NULL DEFAULT 1 CONSTRAINT cheques_row_version_chk CHECK (row_version > 0),
    CONSTRAINT cheques_ref_unique UNIQUE (cheque_ref),
    CONSTRAINT cheques_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT cheques_direction_chk CHECK (direction IN ('in', 'out')),
    CONSTRAINT cheques_status_chk CHECK (
        (direction = 'in' AND status IN ('draft', 'portfolio', 'under_collection', 'collected', 'bounced',
                                         'endorsed', 'returned'))
        OR (direction = 'out' AND status IN ('draft', 'issued', 'cleared', 'cancelled'))),
    CONSTRAINT cheques_amount_chk CHECK (amount > 0),
    CONSTRAINT cheques_no_chk CHECK (btrim(cheque_no) <> ''),
    CONSTRAINT cheques_desc_chk CHECK (btrim(description) <> ''),
    CONSTRAINT cheques_party_chk CHECK (
        (direction = 'in' AND customer_id IS NOT NULL AND drawer_bank_id IS NOT NULL
         AND (status = 'endorsed') = (supplier_id IS NOT NULL))
        OR (direction = 'out' AND supplier_id IS NOT NULL AND bank_account_id IS NOT NULL
            AND customer_id IS NULL AND drawer_bank_id IS NULL)),
    CONSTRAINT cheques_deposit_chk CHECK (direction = 'out'
        OR (status IN ('draft', 'portfolio', 'endorsed') AND bank_account_id IS NULL)
        OR (status IN ('under_collection', 'collected', 'bounced') AND bank_account_id IS NOT NULL)
        OR status = 'returned'),
    CONSTRAINT cheques_posted_chk CHECK (
        (status = 'draft') = (posted_at IS NULL) AND (status = 'draft' OR cheque_ref IS NOT NULL)
        AND (status = 'draft') = (status_date IS NULL))
);
ALTER SEQUENCE cash.cheques_draft_seq OWNED BY cash.cheques.draft_no;
COMMENT ON TABLE cash.cheques IS
    'الشيكات: وارد من العملاء / صادر للموردين · مسودة ← استلام / إصدار ← الخطوات (cash.cheque_events) بقيد لكل خطوة';

-- رقم الشيك مايتكررش على نفس البنك (بنك الساحب للوارد · حسابنا للصادر).
CREATE UNIQUE INDEX cheques_in_no_unique ON cash.cheques (drawer_bank_id, upper(btrim(cheque_no))) WHERE direction = 'in';
CREATE UNIQUE INDEX cheques_out_no_unique ON cash.cheques (bank_account_id, upper(btrim(cheque_no))) WHERE direction = 'out';
-- القائمة بالفترة (الأحدث فوق) · التنبيهات (المفتوح بالاستحقاق) · index على كل FK وعلى البحث.
CREATE INDEX cheques_date_idx ON cash.cheques (direction, cheque_date DESC, id DESC);
CREATE INDEX cheques_due_idx ON cash.cheques (direction, status, due_date);
CREATE INDEX cheques_year_idx ON cash.cheques (fiscal_year_id, status);
CREATE INDEX cheques_customer_idx ON cash.cheques (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX cheques_supplier_idx ON cash.cheques (supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX cheques_drawer_bank_idx ON cash.cheques (drawer_bank_id) WHERE drawer_bank_id IS NOT NULL;
CREATE INDEX cheques_bank_account_idx ON cash.cheques (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX cheques_project_idx ON cash.cheques (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX cheques_created_by_idx ON cash.cheques (created_by);
CREATE INDEX cheques_posted_by_idx ON cash.cheques (posted_by);
CREATE INDEX cheques_no_idx ON cash.cheques (cheque_no varchar_pattern_ops);
CREATE INDEX cheques_desc_idx ON cash.cheques (lower(description) varchar_pattern_ops);

CREATE TABLE cash.cheque_events (
    id               integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cheque_id        integer        NOT NULL REFERENCES cash.cheques (id) ON DELETE RESTRICT,
    seq              smallint       NOT NULL,                        -- 1 = الاستلام / الإصدار
    event_type       varchar(15)    NOT NULL,
    event_date       date           NOT NULL,
    from_status      varchar(20)    NOT NULL,
    to_status        varchar(20)    NOT NULL,
    has_entry        boolean        NOT NULL,
    bank_account_id  integer        REFERENCES cash.bank_accounts (id) ON DELETE RESTRICT,  -- الإيداع · التحصيل · مصاريف الرجوع · الصرف
    supplier_id      integer        REFERENCES party.suppliers (id) ON DELETE RESTRICT,     -- التظهير
    fees             numeric(18,2)  NOT NULL DEFAULT 0,              -- مصاريف بنكية (التحصيل · الرجوع)
    note             varchar(300),
    created_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at       timestamptz    NOT NULL DEFAULT now(),
    CONSTRAINT cheque_events_seq_unique UNIQUE (cheque_id, seq),
    CONSTRAINT cheque_events_seq_chk CHECK (seq > 0),
    CONSTRAINT cheque_events_fees_chk CHECK (fees >= 0 AND (fees = 0 OR event_type IN ('collect', 'bounce'))),
    CONSTRAINT cheque_events_note_chk CHECK (note IS NULL OR btrim(note) <> '')
);
CREATE INDEX cheque_events_bank_idx ON cash.cheque_events (bank_account_id) WHERE bank_account_id IS NOT NULL;
CREATE INDEX cheque_events_supplier_idx ON cash.cheque_events (supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX cheque_events_created_by_idx ON cash.cheque_events (created_by);
CREATE INDEX cheque_events_date_idx ON cash.cheque_events (event_date);
COMMENT ON TABLE cash.cheque_events IS
    'خطوات الشيك (استلام · إيداع · تحصيل · رجوع · تظهير · رد · إصدار · صرف · إلغاء) — كل خطوة ليها قيدها بتاريخها';

-- ---------------------------------------------------------------------------
-- 5) الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION cash.cheque_label(p_direction text, p_ref text, p_draft integer) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_direction WHEN 'in' THEN 'الشيك الوارد ' ELSE 'الشيك الصادر ' END
           || coalesce(p_ref, 'مسودة ' || p_draft)
$$;

-- الحساب البنكي بتاعنا: نشط وبالعملة الأساسية.
CREATE FUNCTION cash.cheque_bank_account_check(p_bank integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    m     record;
    base  text;
BEGIN
    SELECT code INTO base FROM core.currencies WHERE is_base;
    SELECT ba.code, b.name_ar, ba.is_active AND b.is_active AS is_active, ba.currency_code INTO m
      FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id
     WHERE ba.id = p_bank FOR SHARE OF ba;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الحساب البنكي مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
    END IF;
    IF NOT m.is_active THEN
        RAISE EXCEPTION 'الحساب البنكي «% %» موقوف — اختار حساب نشط.', m.code, m.name_ar;
    END IF;
    IF m.currency_code IS DISTINCT FROM base THEN
        RAISE EXCEPTION '«% %» بعملة % — الشيكات بالعملة الأساسية (%) بس.', m.code, m.name_ar, m.currency_code, base;
    END IF;
END $$;

CREATE FUNCTION cash.cheques_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    lbl      text;
    posting  boolean := false;
    m        record;
    doc      text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := cash.cheque_label(OLD.direction, OLD.cheque_ref, OLD.draft_no);
    END IF;

    IF TG_OP = 'DELETE' THEN
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — شيكاتها ماتتمسحش.', oy.year_code;
        END IF;
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION '% اترحّل — مايتمسحش (اعمل «تراجع» لحد ما يرجع مسودة).', lbl;
        END IF;
        IF OLD.cheque_ref IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.draft_no <> OLD.draft_no OR NEW.direction <> OLD.direction
           OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.cheque_ref IS NOT NULL AND NEW.cheque_ref IS DISTINCT FROM OLD.cheque_ref) THEN
            RAISE EXCEPTION 'رقم الشيك ونوعه ثابتين.';
        END IF;
        -- بعد الترحيل: البيانات ثابتة — الحالة وتاريخها وبنك الإيداع والمظهَّر له بس (بالخطوات).
        IF OLD.status <> 'draft' THEN
            IF NEW.status = 'draft' THEN
                -- رجع مسودة (التراجع عن الاستلام / الإصدار): الشيك بيرجع برقمه ويتعدّل.
                IF (to_jsonb(NEW) - 'status' - 'status_date' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at')
                   IS DISTINCT FROM
                   (to_jsonb(OLD) - 'status' - 'status_date' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                    RAISE EXCEPTION 'التراجع بيرجّع الشيك مسودة بس — التعديل بعد كده.';
                END IF;
                NEW.posted_at := NULL;
                NEW.posted_by := NULL;
                NEW.status_date := NULL;
                NEW.updated_at := now();
                RETURN NEW;
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'status_date' - 'bank_account_id' - 'supplier_id' - 'row_version'
                - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'status_date' - 'bank_account_id' - 'supplier_id' - 'row_version'
                - 'updated_at') THEN
                RAISE EXCEPTION '% اترحّل — بياناته ماتتعدّلش (اعمل «تراجع» لحد ما يرجع مسودة).', lbl;
            END IF;
            IF NEW.direction = 'out' AND (NEW.bank_account_id, NEW.supplier_id)
                                          IS DISTINCT FROM (OLD.bank_account_id, OLD.supplier_id) THEN
                RAISE EXCEPTION '% صادر — الحساب البنكي والمورد ثابتين.', lbl;
            END IF;
            IF NEW.status <> OLD.status AND NOT EXISTS (
                   SELECT 1 FROM cash.cheque_transitions t WHERE t.direction = NEW.direction
                      AND ((t.from_status = OLD.status AND t.to_status = NEW.status)
                           OR (t.from_status = NEW.status AND t.to_status = OLD.status))) THEN
                RAISE EXCEPTION '% — مينفعش يتنقل من «%» لـ «%» على طول.', lbl, OLD.status, NEW.status;
            END IF;
            IF NEW.bank_account_id IS NOT NULL AND NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id THEN
                PERFORM cash.cheque_bank_account_check(NEW.bank_account_id);
            END IF;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        -- لسه مسودة: السنة القديمة لازم مفتوحة.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — شيكاتها ماتتعدّلش.', oy.year_code;
        END IF;
    END IF;

    -- مسودة (إضافة / تعديل / ترحيل): السنة من تاريخ الاستلام / الإصدار ولازم مفتوحة.
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.cheque_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.cheque_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها شيك.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.cheque_ref IS NOT NULL THEN
            RAISE EXCEPTION 'الشيك الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        NEW.status_date := NULL;
    ELSE
        IF OLD.cheque_ref IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
            RAISE EXCEPTION '% رقمه من سنة % — تاريخه لازم يفضل في نفس السنة.', lbl, oy.year_code;
        END IF;
        IF NEW.status <> 'draft' THEN
            IF NEW.status <> (CASE NEW.direction WHEN 'in' THEN 'portfolio' ELSE 'issued' END) THEN
                RAISE EXCEPTION '% — المسودة بتترحّل «استلام» / «إصدار» الأول.', lbl;
            END IF;
            posting := true;
            doc := CASE NEW.direction WHEN 'in' THEN 'cheque_in' ELSE 'cheque_out' END;
            IF NEW.cheque_ref IS NULL THEN
                NEW.cheque_ref := core.next_document_no(doc, NEW.cheque_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
            NEW.status_date := NEW.cheque_date;
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.status_date := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- الطرف · البنك · أمر التوريد: وقت الإضافة أو التغيير أو الترحيل.
    IF NEW.direction = 'in' THEN
        IF TG_OP = 'INSERT' OR posting OR NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
            SELECT code, name_ar, is_active INTO m FROM party.customers WHERE id = NEW.customer_id FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'العميل مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
            END IF;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'العميل «% %» موقوف — اختار عميل نشط.', m.code, m.name_ar;
            END IF;
        END IF;
        IF TG_OP = 'INSERT' OR posting OR NEW.drawer_bank_id IS DISTINCT FROM OLD.drawer_bank_id THEN
            SELECT code, name_ar, is_active INTO m FROM cash.banks WHERE id = NEW.drawer_bank_id FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'بنك الساحب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
            END IF;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'البنك «%» موقوف — اختار بنك نشط.', m.name_ar;
            END IF;
        END IF;
    ELSE
        IF TG_OP = 'INSERT' OR posting OR NEW.supplier_id IS DISTINCT FROM OLD.supplier_id THEN
            SELECT code, name_ar, is_active INTO m FROM party.suppliers WHERE id = NEW.supplier_id FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'المورد مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
            END IF;
            IF NOT m.is_active THEN
                RAISE EXCEPTION 'المورد «% %» موقوف — اختار مورد نشط.', m.code, m.name_ar;
            END IF;
        END IF;
        IF TG_OP = 'INSERT' OR posting OR NEW.bank_account_id IS DISTINCT FROM OLD.bank_account_id THEN
            PERFORM cash.cheque_bank_account_check(NEW.bank_account_id);
        END IF;
    END IF;
    IF NEW.project_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR posting OR NEW.project_id IS DISTINCT FROM OLD.project_id) THEN
        SELECT code, status INTO m FROM acc.projects WHERE id = NEW.project_id;
        IF m.status <> 'open' THEN
            RAISE EXCEPTION 'أمر التوريد «%» مش جاري (مقفول أو ملغي).', m.code;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- الخطوة: مسموحة من الحالة الحالية · رقمها بعد آخر خطوة · تاريخها مش قبل اللي قبلها وفي سنة مفتوحة ·
-- مابتتعدّلش · آخر خطوة بس هي اللي تتمسح (= «تراجع»).
CREATE FUNCTION cash.cheque_events_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    c     cash.cheques%ROWTYPE;
    t     cash.cheque_transitions%ROWTYPE;
    y     core.fiscal_years%ROWTYPE;
    prev  cash.cheque_events%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'خطوات الشيك مابتتعدّلش — اعمل «تراجع» عن آخر خطوة وسجّلها تاني.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM cash.cheque_events WHERE cheque_id = OLD.cheque_id AND seq > OLD.seq) THEN
            RAISE EXCEPTION 'التراجع بيبقى عن آخر خطوة بس.';
        END IF;
        SELECT * INTO y FROM core.fiscal_years WHERE OLD.event_date BETWEEN start_date AND end_date FOR SHARE;
        IF y.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — الخطوة اللي بتاريخ % ماينفعش التراجع عنها.', y.year_code,
                to_char(OLD.event_date, 'DD/MM/YYYY');
        END IF;
        RETURN OLD;
    END IF;

    SELECT * INTO c FROM cash.cheques WHERE id = NEW.cheque_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الشيك مش موجود (ممكن يكون اتمسح) — دوس «تحديث».';
    END IF;
    SELECT * INTO t FROM cash.cheque_transitions
     WHERE direction = c.direction AND event_type = NEW.event_type AND from_status = NEW.from_status;
    IF NOT FOUND OR t.to_status <> NEW.to_status OR t.has_entry <> NEW.has_entry THEN
        RAISE EXCEPTION '% — الخطوة دي مش مسموحة من الحالة دي.', cash.cheque_label(c.direction, c.cheque_ref, c.draft_no);
    END IF;
    SELECT * INTO prev FROM cash.cheque_events WHERE cheque_id = NEW.cheque_id ORDER BY seq DESC LIMIT 1;
    IF NEW.seq <> coalesce(prev.seq, 0) + 1 THEN
        RAISE EXCEPTION 'رقم الخطوة لازم يبقى بعد آخر خطوة على طول.';
    END IF;
    IF NEW.from_status <> coalesce(prev.to_status, 'draft') THEN
        RAISE EXCEPTION '% حالته اتغيرت من جهاز تاني — دوس «تحديث».', cash.cheque_label(c.direction, c.cheque_ref, c.draft_no);
    END IF;
    IF NEW.seq = 1 AND NEW.event_date <> c.cheque_date THEN
        RAISE EXCEPTION 'تاريخ الاستلام / الإصدار لازم يبقى تاريخ الشيك.';
    END IF;
    IF NEW.event_date < c.cheque_date OR NEW.event_date < coalesce(prev.event_date, c.cheque_date) THEN
        RAISE EXCEPTION 'تاريخ الخطوة (%) قبل الخطوة اللي قبلها (%) — اختار تاريخ بعدها.',
            to_char(NEW.event_date, 'DD/MM/YYYY'), to_char(coalesce(prev.event_date, c.cheque_date), 'DD/MM/YYYY');
    END IF;
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.event_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.event_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل فيها خطوة.', y.year_code;
    END IF;
    -- البيانات اللي الخطوة محتاجاها.
    IF NEW.event_type IN ('deposit', 'collect') AND NEW.bank_account_id IS NULL THEN
        RAISE EXCEPTION 'اختار الحساب البنكي اللي الشيك اتودع فيه.';
    END IF;
    IF NEW.event_type = 'endorse' AND NEW.supplier_id IS NULL THEN
        RAISE EXCEPTION 'اختار المورد اللي الشيك اتظهّر له.';
    END IF;
    IF NEW.event_type = 'endorse' THEN
        IF NOT (SELECT is_active FROM party.suppliers WHERE id = NEW.supplier_id FOR SHARE) THEN
            RAISE EXCEPTION 'المورد ده موقوف — اختار مورد نشط.';
        END IF;
    END IF;
    IF NEW.bank_account_id IS NOT NULL THEN
        PERFORM cash.cheque_bank_account_check(NEW.bank_account_id);
    END IF;
    IF NEW.fees > 0 AND NEW.fees >= c.amount AND NEW.event_type = 'collect' THEN
        RAISE EXCEPTION 'المصاريف البنكية (%) أكبر من مبلغ الشيك (%).', NEW.fees, c.amount;
    END IF;
    RETURN NEW;
END $$;

-- آخر الـ transaction: حالة الشيك = حالة آخر خطوة · كل خطوة ليها قيد مرحّل بتاريخها ومبلغها (ومن غير قيد لو مالهاش)
-- · الخطوة اللي اتمسحت قيدها اتمسح.
CREATE FUNCTION cash.cheque_state_check(p_cheque integer) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    c     cash.cheques%ROWTYPE;
    ev    cash.cheque_events%ROWTYPE;
    e     record;
    doc   text;
    want  text;
    lbl   text;
BEGIN
    SELECT * INTO c FROM cash.cheques WHERE id = p_cheque;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    lbl := cash.cheque_label(c.direction, c.cheque_ref, c.draft_no);
    doc := CASE c.direction WHEN 'in' THEN 'cheque_in' ELSE 'cheque_out' END;
    SELECT * INTO ev FROM cash.cheque_events WHERE cheque_id = p_cheque ORDER BY seq DESC LIMIT 1;
    want := coalesce(ev.to_status, 'draft');
    IF c.status <> want THEN
        RAISE EXCEPTION '% حالته «%» وآخر خطوة بتقول «%» — الخطوة والحالة لازم يتغيروا مع بعض.', lbl, c.status, want;
    END IF;
    IF ev.id IS NOT NULL AND c.status_date IS DISTINCT FROM ev.event_date THEN
        RAISE EXCEPTION '% تاريخ حالته مش زي تاريخ آخر خطوة.', lbl;
    END IF;
    FOR ev IN SELECT * FROM cash.cheque_events WHERE cheque_id = p_cheque ORDER BY seq LOOP
        SELECT status, total_debit, entry_date INTO e FROM acc.journal_entries
         WHERE source_type = doc AND source_id = ev.id;
        IF ev.has_entry THEN
            IF e.status IS DISTINCT FROM 'posted' THEN
                RAISE EXCEPTION '% — الخطوة % من غير قيد مرحّل.', lbl, ev.seq;
            END IF;
            IF e.total_debit <> c.amount + (CASE ev.event_type WHEN 'bounce' THEN ev.fees ELSE 0 END) THEN
                RAISE EXCEPTION '% — قيد الخطوة % مبلغه % مش زي مبلغ الشيك %.', lbl, ev.seq, e.total_debit, c.amount;
            END IF;
            IF e.entry_date <> ev.event_date THEN
                RAISE EXCEPTION '% — قيد الخطوة % تاريخه مش زي تاريخ الخطوة.', lbl, ev.seq;
            END IF;
        ELSIF e.status IS NOT NULL THEN
            RAISE EXCEPTION '% — الخطوة % مالهاش قيد.', lbl, ev.seq;
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION cash.cheques_state_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cash.cheque_state_check(NEW.id);
    RETURN NULL;
END $$;

CREATE FUNCTION cash.cheque_events_state_trg() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    doc  text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT CASE direction WHEN 'in' THEN 'cheque_in' ELSE 'cheque_out' END INTO doc
          FROM cash.cheques WHERE id = OLD.cheque_id;
        IF EXISTS (SELECT 1 FROM acc.journal_entries WHERE source_type = doc AND source_id = OLD.id) THEN
            RAISE EXCEPTION 'التراجع عن الخطوة لازم يمسح قيدها في نفس العملية.';
        END IF;
        PERFORM cash.cheque_state_check(OLD.cheque_id);
    ELSE
        PERFORM cash.cheque_state_check(NEW.cheque_id);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER cheques_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.cheques
    FOR EACH ROW EXECUTE FUNCTION cash.cheques_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON cash.cheques
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER cheques_state_trg AFTER INSERT OR UPDATE ON cash.cheques
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.cheques_state_trg();
CREATE TRIGGER cheque_events_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON cash.cheque_events
    FOR EACH ROW EXECUTE FUNCTION cash.cheque_events_guard();
CREATE CONSTRAINT TRIGGER cheque_events_state_trg AFTER INSERT OR DELETE ON cash.cheque_events
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cash.cheque_events_state_trg();

-- ---------------------------------------------------------------------------
-- 6) السنة المالية: نفس حماية السندات والحركات + الشيكات (كل جدول بعمود تاريخه)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.fiscal_years_voucher_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    t  text[];
    c  integer;
    n  integer := 0;
    docs constant text[][] := ARRAY[['cash.customer_receipts', 'voucher_date'], ['cash.supplier_payments', 'voucher_date'],
                                    ['cash.customer_payments', 'voucher_date'], ['cash.cash_transfers', 'transfer_date'],
                                    ['cash.cheques', 'cheque_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
