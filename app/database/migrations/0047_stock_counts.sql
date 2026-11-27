-- 0047: «الجرد والتسويات» (المرحلة 6 — النموذج 1 «الكلاسيك»، اختيار المستخدم 2026-11-27
--       — mockups/phase6/03_stock_count.html).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (المستخدم اختار النموذج 1، وباقي الأسئلة اتعملت بالاقتراح):
--   * **الجرد مستند لمخزن واحد**: السطر = صنف + نوع (فاتورة / بيان) + **الفعلي** بالوحدة الأساسية. الرصيد الدفتري
--     بيتحسب من دفتر المخزون (0043) **آخر يوم الجرد** وقت الترحيل، والفرق بيتسجّل حركة «تسوية جرد»: العجز خروج
--     بالمتوسط · الزيادة دخول بالمتوسط (ولو الصنف مالوش متوسط التكلفة بتتكتب على السطر) · المطابق مالوش حركة.
--   * **اللي مااتعدّش بيفضل برصيده** — إلا لو «اللي مااتعدّش = صفر» (الجرد الشامل): الترحيل بيعمله سطر بصفر لوحده.
--   * **تسوية مباشرة** (نوع من أنواع المستند — من غير جرد): السطر = صنف + نوع + كمية ± + السبب (تالف · كسر · عينات ·
--     فرق عد · منتهي).
--   * أنواع الجرد (دوري · سنوي · جزئي) تصنيف للتقارير بس.
--   * **مسودة ← ترحيل** والرقم SC-2026-00001 من أول ترحيل (ويبدأ من 1 كل سنة) · «إلغاء الترحيل» بالصلاحية في السنة
--     المفتوحة والرقم ثابت · المسح للمسودة اللي ماخدتش رقم بس.
--   * القيد (قواعد 0032 على «تسوية جرد»): الزيادة مدين المخزون (من نوع الصنف) / دائن «تسوية جرد المخزون» · العجز
--     العكس (نفس القواعد بمبلغ سالب) · **المخزن الوهمي كميات بس من غير قيد**.
--   * **كل سطر بإصداره** (2–3 أجهزة بيعدّوا نفس المخزن في نفس الجرد): السطر بيتحفظ لوحده، والرأس مابيتغيّرش غير لو
--     بياناته هي اللي اتغيّرت.
-- الحماية في القاعدة نفسها (زي التحويل بين المخازن):
--   * السنة من التاريخ ولازم مفتوحة · المرحّل مايتعدّلش ولا يتمسح ولا سطوره · الرقم ثابت وفي نفس السنة.
--   * المخزن موجود ونشط وقت الإضافة أو التغيير أو الترحيل · الصنف مش خدمي (والموقوف مايتضافش).
--   * سطر الجرد فيه «الفعلي» بس، وسطر التسوية المباشرة فيه «الكمية ±» والسبب بس.
--   * **المرحّل: كل سطوره عليها الدفتري والفرق، وحركة مخزون لكل سطر فرقه مش صفر، وقيد مرحّل بقيمتها · والمسودة
--     مالهاش ولا ده ولا ده** (Constraint Trigger مؤجل).
--   * السنة ماتتقفلش وفيها جرود مسودة، وتواريخها ماتسيبش جرد برّاها.

-- ---------------------------------------------------------------------------
-- 1) الترقيم (نوع المستند وقواعد الترحيل «stock_adjustment» موجودين من 0032 و 0043)
-- ---------------------------------------------------------------------------
INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('stock_count', 'جرد وتسوية مخزون', 'SC', 5);

-- ---------------------------------------------------------------------------
-- 2) الجداول
-- ---------------------------------------------------------------------------
CREATE SEQUENCE inv.stock_counts_draft_seq AS integer;
CREATE TABLE inv.stock_counts (
    id              integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    count_no        varchar(30),                                   -- SC-2026-00001 (من أول ترحيل)
    draft_no        integer        NOT NULL DEFAULT nextval('inv.stock_counts_draft_seq'),
    count_date      date           NOT NULL,
    fiscal_year_id  integer        NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    warehouse_id    integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    count_type      varchar(10)    NOT NULL DEFAULT 'periodic',     -- periodic دوري · annual سنوي · partial جزئي · direct تسوية مباشرة
    zero_uncounted  boolean        NOT NULL DEFAULT false,          -- اللي مااتعدّش = صفر (الجرد الشامل)
    description     varchar(500),                                   -- ملاحظات (اختياري)
    status          varchar(10)    NOT NULL DEFAULT 'draft',
    created_by      integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at       timestamptz,
    posted_by       integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at      timestamptz    NOT NULL DEFAULT now(),
    updated_at      timestamptz    NOT NULL DEFAULT now(),
    row_version     integer        NOT NULL DEFAULT 1 CONSTRAINT stock_counts_row_version_chk CHECK (row_version > 0),
    CONSTRAINT stock_counts_no_unique UNIQUE (count_no),
    CONSTRAINT stock_counts_draft_no_unique UNIQUE (draft_no),
    CONSTRAINT stock_counts_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT stock_counts_type_chk CHECK (count_type IN ('periodic', 'annual', 'partial', 'direct')),
    CONSTRAINT stock_counts_zero_chk CHECK (NOT (zero_uncounted AND count_type = 'direct')),
    CONSTRAINT stock_counts_desc_chk CHECK (description IS NULL OR btrim(description) <> ''),
    CONSTRAINT stock_counts_posted_chk CHECK (
        (status = 'posted') = (posted_at IS NOT NULL) AND (status = 'draft' OR count_no IS NOT NULL))
);
ALTER SEQUENCE inv.stock_counts_draft_seq OWNED BY inv.stock_counts.draft_no;
COMMENT ON TABLE inv.stock_counts IS
    'الجرد والتسويات: جرد مخزن (الفعلي قصاد الدفتري) أو تسوية مباشرة · مسودة ← مرحّل بحركات «تسوية جرد» وقيدها';

CREATE TABLE inv.stock_count_lines (
    id             integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    count_id       integer        NOT NULL REFERENCES inv.stock_counts (id) ON DELETE CASCADE,
    item_id        integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    stock_kind     varchar(10)    NOT NULL DEFAULT 'invoice',      -- invoice فاتورة · statement بيان
    counted_qty    numeric(18,6),                                  -- الفعلي بالوحدة الأساسية (الجرد)
    adjust_qty     numeric(18,6),                                  -- الكمية ± (التسوية المباشرة)
    reason         varchar(20),                                    -- سبب التسوية المباشرة
    unit_cost      numeric(18,6),                                  -- تكلفة الزيادة لو الصنف مالوش متوسط
    seen_book_qty  numeric(18,6),                                  -- الدفتري وقت ما السطر اتحفظ (للتنبيه لو اتغيّر)
    book_qty       numeric(18,6),                                  -- الدفتري وقت الترحيل (المرحّل بس)
    diff_qty       numeric(18,6),                                  -- الفرق اللي اترحّل (+ زيادة · − عجز)
    auto_zero      boolean        NOT NULL DEFAULT false,          -- اتعمل لوحده من «اللي مااتعدّش = صفر»
    updated_by     integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    updated_at     timestamptz    NOT NULL DEFAULT now(),
    row_version    integer        NOT NULL DEFAULT 1 CONSTRAINT stock_count_lines_row_version_chk CHECK (row_version > 0),
    CONSTRAINT stock_count_lines_item_unique UNIQUE (count_id, item_id, stock_kind),
    CONSTRAINT stock_count_lines_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT stock_count_lines_shape_chk CHECK ((counted_qty IS NULL) <> (adjust_qty IS NULL)),
    CONSTRAINT stock_count_lines_counted_chk CHECK (counted_qty IS NULL OR counted_qty >= 0),
    CONSTRAINT stock_count_lines_adjust_chk CHECK (adjust_qty IS NULL OR adjust_qty <> 0),
    CONSTRAINT stock_count_lines_reason_chk CHECK (
        reason IS NULL OR reason IN ('damaged', 'broken', 'samples', 'count_diff', 'expired')),
    CONSTRAINT stock_count_lines_reason_need_chk CHECK ((adjust_qty IS NULL) = (reason IS NULL)),
    CONSTRAINT stock_count_lines_cost_chk CHECK (unit_cost IS NULL OR unit_cost >= 0),
    CONSTRAINT stock_count_lines_posted_chk CHECK ((book_qty IS NULL) = (diff_qty IS NULL)),
    CONSTRAINT stock_count_lines_auto_chk CHECK (NOT auto_zero OR counted_qty = 0)
);
COMMENT ON TABLE inv.stock_count_lines IS
    'سطور الجرد: الفعلي لكل (صنف + نوع) — أو الكمية ± والسبب في التسوية المباشرة · الدفتري والفرق بيتكتبوا مع الترحيل';

-- القائمة بالفترة (الأحدث فوق) + index على كل FK وعلى البحث.
CREATE INDEX stock_counts_date_idx ON inv.stock_counts (count_date DESC, id DESC);
CREATE INDEX stock_counts_year_idx ON inv.stock_counts (fiscal_year_id, status);
CREATE INDEX stock_counts_warehouse_idx ON inv.stock_counts (warehouse_id, count_date DESC);
CREATE INDEX stock_counts_created_by_idx ON inv.stock_counts (created_by);
CREATE INDEX stock_counts_posted_by_idx ON inv.stock_counts (posted_by);
CREATE INDEX stock_counts_desc_idx ON inv.stock_counts (lower(description) varchar_pattern_ops)
    WHERE description IS NOT NULL;
CREATE INDEX stock_count_lines_item_idx ON inv.stock_count_lines (item_id);
CREATE INDEX stock_count_lines_updated_by_idx ON inv.stock_count_lines (updated_by);

-- ---------------------------------------------------------------------------
-- 3) الحماية
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.stock_counts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y        core.fiscal_years%ROWTYPE;
    oy       core.fiscal_years%ROWTYPE;
    w        record;
    lbl      text;
    posting  boolean;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        lbl := 'الجرد ' || coalesce(OLD.count_no, 'مسودة ' || OLD.draft_no);
        -- FOR SHARE: محدش يقفل السنة وإحنا بنعدّل فيها.
        SELECT * INTO oy FROM core.fiscal_years WHERE id = OLD.fiscal_year_id FOR SHARE;
        IF oy.status = 'closed' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — جرودها ماتتعدّلش ولا تتمسح ولا يتلغى ترحيلها.', oy.year_code;
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'posted' THEN
            RAISE EXCEPTION '% مرحّل — مايتمسحش (الغي ترحيله الأول).', lbl;
        END IF;
        IF OLD.count_no IS NOT NULL THEN
            RAISE EXCEPTION '% خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).', lbl;
        END IF;
        RETURN OLD;
    END IF;

    -- السنة المالية من التاريخ (لازم مفتوحة).
    SELECT * INTO y FROM core.fiscal_years WHERE NEW.count_date BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(NEW.count_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها جرد.', y.year_code;
    END IF;
    NEW.fiscal_year_id := y.id;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' OR NEW.count_no IS NOT NULL THEN
            RAISE EXCEPTION 'الجرد الجديد بيتسجل مسودة الأول (وبعدين يترحّل).';
        END IF;
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
        posting := false;
    ELSE
        IF NEW.draft_no <> OLD.draft_no OR NEW.created_by IS DISTINCT FROM OLD.created_by
           OR (OLD.count_no IS NOT NULL AND NEW.count_no IS DISTINCT FROM OLD.count_no) THEN
            RAISE EXCEPTION 'رقم الجرد ثابت.';
        END IF;
        IF OLD.count_no IS NOT NULL AND NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
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
            -- إلغاء الترحيل: الحالة بس (الجرد بيرجع مسودة برقمه).
            IF (to_jsonb(NEW) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'posted_at' - 'posted_by' - 'row_version' - 'updated_at') THEN
                RAISE EXCEPTION 'إلغاء الترحيل بيرجّع الجرد مسودة بس — التعديل بعد كده.';
            END IF;
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
            NEW.updated_at := now();
            RETURN NEW;
        END IF;
        -- نوع المستند (جرد ↔ تسوية مباشرة) مايتغيّرش وعليه سطور بالشكل التاني.
        IF (NEW.count_type = 'direct') <> (OLD.count_type = 'direct')
           AND EXISTS (SELECT 1 FROM inv.stock_count_lines WHERE count_id = NEW.id) THEN
            RAISE EXCEPTION '% عليه سطور — امسحها الأول قبل ما تغيّر نوعه بين «جرد» و«تسوية مباشرة».', lbl;
        END IF;
        posting := NEW.status = 'posted';
        IF posting THEN
            IF NOT NEW.zero_uncounted AND NOT EXISTS (SELECT 1 FROM inv.stock_count_lines WHERE count_id = NEW.id) THEN
                RAISE EXCEPTION '% مافيهوش ولا صنف — اكتب الفعلي لصنف واحد على الأقل قبل الترحيل.', lbl;
            END IF;
            IF NEW.count_no IS NULL THEN
                NEW.count_no := core.next_document_no('stock_count', NEW.count_date);   -- جوه قفل العدّاد
            END IF;
            NEW.posted_at := now();
        ELSE
            NEW.posted_at := NULL;
            NEW.posted_by := NULL;
        END IF;
        NEW.updated_at := now();
    END IF;

    -- المخزن: موجود ونشط — وقت الإضافة أو التغيير أو الترحيل.
    IF TG_OP = 'INSERT' OR posting OR NEW.warehouse_id IS DISTINCT FROM OLD.warehouse_id THEN
        SELECT code, name_ar, is_active INTO w FROM inv.warehouses WHERE id = NEW.warehouse_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'المخزن مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NOT w.is_active THEN
            RAISE EXCEPTION 'المخزن «% %» موقوف — اختار مخزن نشط.', w.code, w.name_ar;
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- السطور: الجرد لازم مسودة · الصنف مش خدمي (والموقوف مايتضافش) · شكل السطر على نوع المستند.
CREATE FUNCTION inv.stock_count_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h    record;
    it   record;
BEGIN
    SELECT status, count_no, draft_no, count_type INTO h FROM inv.stock_counts
     WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.count_id ELSE NEW.count_id END;
    IF NOT FOUND THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;   -- الجرد نفسه اتمسح (CASCADE)
    END IF;
    IF h.status <> 'draft' THEN
        RAISE EXCEPTION 'الجرد % مرحّل — سطوره ماتتعدّلش (الغي ترحيله الأول).',
            coalesce(h.count_no, 'مسودة ' || h.draft_no);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.count_id <> OLD.count_id OR NEW.item_id <> OLD.item_id
                             OR NEW.stock_kind <> OLD.stock_kind) THEN
        RAISE EXCEPTION 'سطر الجرد مايتنقلش لصنف أو نوع أو جرد تاني.';
    END IF;
    IF (h.count_type = 'direct') <> (NEW.adjust_qty IS NOT NULL) THEN
        IF h.count_type = 'direct' THEN
            RAISE EXCEPTION 'التسوية المباشرة سطرها «الكمية ±» والسبب — مش «الفعلي».';
        END IF;
        RAISE EXCEPTION 'سطر الجرد فيه «الفعلي» بس — «الكمية ±» للتسوية المباشرة.';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT code, name, is_service, is_active INTO it FROM inv.items WHERE id = NEW.item_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الصنف مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF it.is_service THEN
            RAISE EXCEPTION 'الصنف % «%» خدمي — مالوش رصيد يتجرد.', it.code, it.name;
        END IF;
        IF NOT it.is_active AND NOT NEW.auto_zero THEN
            RAISE EXCEPTION 'الصنف % «%» موقوف — مايتضافش للجرد.', it.code, it.name;
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;

-- المرحّل: الدفتري والفرق على كل سطر + حركة لكل فرق + قيد مرحّل بقيمتها · المسودة مالهاش (آخر الـ transaction).
CREATE FUNCTION inv.stock_counts_post_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v      record;
    e      record;
    nd     integer;
    nb     integer;
    na     integer;
    nm     integer;
    total  numeric;
    lbl    text;
BEGIN
    SELECT id, status, count_no, draft_no INTO v FROM inv.stock_counts WHERE id = NEW.id;
    IF v.id IS NULL THEN
        RETURN NULL;                                     -- اتمسح في نفس الـ transaction
    END IF;
    lbl := coalesce(v.count_no, 'مسودة ' || v.draft_no);
    SELECT count(*) FILTER (WHERE diff_qty IS DISTINCT FROM 0 AND diff_qty IS NOT NULL),
           count(*) FILTER (WHERE book_qty IS NULL), count(*) FILTER (WHERE auto_zero)
      INTO nd, nb, na FROM inv.stock_count_lines WHERE count_id = v.id;
    SELECT count(*), coalesce(sum(abs(booked_value)) FILTER (WHERE NOT is_virtual), 0) INTO nm, total
      FROM inv.stock_movements WHERE source_type = 'stock_adjustment' AND source_id = v.id;
    SELECT status, total_debit INTO e FROM acc.journal_entries
     WHERE source_type = 'stock_adjustment' AND source_id = v.id;
    IF v.status = 'posted' THEN
        IF nb > 0 THEN
            RAISE EXCEPTION 'الجرد % مرحّل وفيه سطور من غير الرصيد الدفتري — الترحيل لازم يحسبه في نفس العملية.', lbl;
        END IF;
        IF nm <> nd THEN
            RAISE EXCEPTION 'الجرد % مرحّل من غير حركات المخزون بتاعة فروقه — الترحيل لازم يعملها في نفس العملية.', lbl;
        END IF;
        IF total <> 0 AND e.status IS DISTINCT FROM 'posted' THEN
            RAISE EXCEPTION 'الجرد % مرحّل من غير قيد — الترحيل لازم يعمل القيد في نفس العملية.', lbl;
        END IF;
        IF e.status IS NOT NULL AND e.total_debit <> total THEN
            RAISE EXCEPTION 'قيد الجرد % مبلغه % مش زي قيمة الحركات %.', lbl, e.total_debit, total;
        END IF;
    ELSIF nm > 0 OR e.status IS NOT NULL OR nb < (SELECT count(*) FROM inv.stock_count_lines WHERE count_id = v.id)
          OR na > 0 THEN
        RAISE EXCEPTION 'الجرد % مسودة وليه حركات مخزون أو قيد أو فروق مترحّلة — إلغاء الترحيل لازم يمسحهم في نفس العملية.',
            lbl;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER stock_counts_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON inv.stock_counts
    FOR EACH ROW EXECUTE FUNCTION inv.stock_counts_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.stock_counts
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
CREATE CONSTRAINT TRIGGER stock_counts_post_trg AFTER INSERT OR UPDATE ON inv.stock_counts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION inv.stock_counts_post_check();
CREATE TRIGGER stock_count_lines_guard_trg BEFORE INSERT OR UPDATE OR DELETE ON inv.stock_count_lines
    FOR EACH ROW EXECUTE FUNCTION inv.stock_count_lines_guard();
CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON inv.stock_count_lines
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- 4) السنة المالية: نفس حماية السندات + التحويلات + الجرود (كل جدول بعمود تاريخه)
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
                                    ['acc.profit_distributions', 'dist_date'], ['inv.stock_transfers', 'transfer_date'],
                                    ['inv.stock_counts', 'count_date']];
BEGIN
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        FOREACH t SLICE 1 IN ARRAY docs LOOP
            CONTINUE WHEN to_regclass(t[1]) IS NULL;
            EXECUTE format('SELECT count(*) FROM %s WHERE fiscal_year_id = $1 AND %I NOT BETWEEN $2 AND $3', t[1], t[2])
                INTO c USING OLD.id, NEW.start_date, NEW.end_date;
            n := n + c;
        END LOOP;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % سند (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود) في السنة % تاريخه برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
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
            RAISE EXCEPTION 'السنة المالية % فيها % سند مسودة (سندات وحركات خزائن وبنوك وشيكات وعهد وشركاء وتحويلات مخازن وجرود) — رحّلهم أو امسحهم قبل القفل.',
                OLD.year_code, n;
        END IF;
    END IF;
    RETURN NEW;
END $$;
