-- 0006: ترقيم المستندات (المرحلة 1ب) — أوتوماتيك من غير شاشة (قرار المستخدم 2026-11-08)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (docs/03 القسم 8 بند 16 + 2026-11-08):
--   * الترقيم **بيبدأ من 1 كل سنة مالية**، والشكل: البادئة-السنة-المسلسل (INV-2026-00001).
--   * **أوتوماتيك**: مفيش شاشة. عدّاد كل (نوع مستند، سنة مالية) بيتعمل لوحده مع أول مستند في السنة.
--   * الرقم بيتصرف **وقت الحفظ** جوه نفس transaction المستند: لو الحفظ فشل الرقم بيرجع (مفيش رقم بيضيع).
-- القاعدة نفسها بتحمي:
--   * الحجز بقفل الصف (INSERT … ON CONFLICT DO UPDATE = نفس قفل FOR UPDATE): جهازين مايخدوش نفس الرقم.
--   * مفيش رقم في سنة مقفولة، ولا في تاريخ مالوش سنة مالية.
--   * العدّاد مابينزلش ولا بيتمسح، والسنة اللي اتصرف فيها أرقام مابتتمسحش (FK RESTRICT).
-- كل موديول مستندات جديد بيضيف نوعه هنا في السكريبت بتاعه لو مش موجود.

-- ---------------------------------------------------------------------------
-- أنواع المستندات (ثابتة من البرنامج)
-- ---------------------------------------------------------------------------
CREATE TABLE core.document_types (
    code               varchar(40)  PRIMARY KEY,               -- sales_invoice, receipt…
    name_ar            varchar(60)  NOT NULL,
    prefix             varchar(6)   NOT NULL,                  -- INV, PUR, RCV…
    digits             smallint     NOT NULL DEFAULT 5,        -- خانات المسلسل (بيكمّل بخانة زيادة لو عدّاها)
    two_step_approval  boolean      NOT NULL DEFAULT false,    -- قرار 4: الافتراضي خطوة واحدة (اعتماد = ترحيل)
    created_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT document_types_code_format CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT document_types_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT document_types_prefix_format CHECK (prefix ~ '^[A-Z][A-Z0-9]{0,5}$'),
    CONSTRAINT document_types_prefix_unique UNIQUE (prefix),
    CONSTRAINT document_types_digits_range CHECK (digits BETWEEN 3 AND 8)
);
COMMENT ON TABLE core.document_types IS 'أنواع المستندات وشكل ترقيمها (البادئة-السنة-المسلسل) — ثابتة من البرنامج';

INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES
    ('sales_invoice',    'فاتورة مبيعات',   'INV', 5),
    ('sales_return',     'مرتجع مبيعات',    'SRT', 4),
    ('purchase_invoice', 'فاتورة مشتريات',  'PUR', 5),
    ('purchase_return',  'مرتجع مشتريات',   'PRT', 4),
    ('receipt',          'سند قبض',         'RCV', 5),
    ('payment',          'سند صرف',         'PAY', 5),
    ('journal',          'قيد يومية',       'JV',  5),
    ('opening',          'أرصدة افتتاحية',  'OPN', 3),
    ('transfer',         'تحويل مخزني',     'TRF', 4),
    ('expense',          'مصروف',           'EXP', 5);

-- ---------------------------------------------------------------------------
-- العدّادات: واحد لكل (نوع، سنة مالية) — بيتعمل لوحده
-- ---------------------------------------------------------------------------
CREATE TABLE core.document_sequences (
    doc_type        varchar(40)  NOT NULL REFERENCES core.document_types (code) ON DELETE RESTRICT,
    fiscal_year_id  integer      NOT NULL REFERENCES core.fiscal_years (id) ON DELETE RESTRICT,
    last_no         integer      NOT NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (doc_type, fiscal_year_id),
    CONSTRAINT document_sequences_last_no_positive CHECK (last_no >= 1)
);
CREATE INDEX document_sequences_year_idx ON core.document_sequences (fiscal_year_id);
COMMENT ON TABLE core.document_sequences IS 'آخر رقم اتصرف لكل (نوع مستند، سنة مالية) — بيبدأ من 1 كل سنة';

-- العدّاد مابينزلش، ومابيتنقلش لنوع أو سنة تانية، ومابيتمسحش (عشان مايتكررش رقم).
CREATE FUNCTION core.document_sequences_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'عدّاد ترقيم المستندات مابيتمسحش (عشان الأرقام ماتتكررش).';
    END IF;
    IF NEW.doc_type <> OLD.doc_type OR NEW.fiscal_year_id <> OLD.fiscal_year_id THEN
        RAISE EXCEPTION 'عدّاد ترقيم المستندات مابيتنقلش لنوع أو سنة تانية.';
    END IF;
    IF NEW.last_no < OLD.last_no THEN
        RAISE EXCEPTION 'عدّاد ترقيم المستندات مابينزلش (من % لـ %) — عشان الأرقام ماتتكررش.', OLD.last_no, NEW.last_no;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER document_sequences_guard_trg
    BEFORE UPDATE OR DELETE ON core.document_sequences
    FOR EACH ROW EXECUTE FUNCTION core.document_sequences_guard();

-- ---------------------------------------------------------------------------
-- شكل الرقم: البادئة-السنة-المسلسل (المسلسل بأصفار على الشمال، ولو عدّى الخانات بيكمّل من غير ما يتقص)
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.format_document_no(p_prefix text, p_year integer, p_digits integer, p_no integer)
RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT p_prefix || '-' || p_year::text || '-' ||
           CASE WHEN length(p_no::text) >= p_digits THEN p_no::text ELSE lpad(p_no::text, p_digits, '0') END
$$;

-- السنة اللي في الرقم = سنة بداية السنة المالية (2026 للسنة 2026، و2026 كمان للسنة 2026/2027).
CREATE FUNCTION core.fiscal_year_label(p_start date) RETURNS integer
LANGUAGE sql IMMUTABLE AS $$ SELECT extract(year FROM p_start)::int $$;

-- صرف الرقم الجاي (بيتنادى جوه transaction الحفظ بتاع المستند).
CREATE FUNCTION core.next_document_no(p_doc_type text, p_doc_date date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    t  core.document_types;
    y  core.fiscal_years;
    n  integer;
BEGIN
    IF p_doc_date IS NULL THEN
        RAISE EXCEPTION 'لازم تاريخ المستند عشان يتصرف رقمه.';
    END IF;
    SELECT * INTO t FROM core.document_types WHERE code = p_doc_type;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'نوع المستند «%» مش متعرّف في الترقيم.', p_doc_type;
    END IF;
    -- FOR SHARE: محدش يقفل السنة أو يمسحها وإحنا بنصرف فيها رقم.
    SELECT * INTO y FROM core.fiscal_years
    WHERE p_doc_date BETWEEN start_date AND end_date
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(p_doc_date, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش يتسجل فيها مستند جديد.', y.year_code;
    END IF;
    -- أول مستند في السنة بيعمل العدّاد بـ 1، والباقي بيزوّد — وفي الحالتين الصف بيتقفل لحد آخر الـ transaction.
    INSERT INTO core.document_sequences AS s (doc_type, fiscal_year_id, last_no)
    VALUES (t.code, y.id, 1)
    ON CONFLICT (doc_type, fiscal_year_id) DO UPDATE SET last_no = s.last_no + 1
    RETURNING s.last_no INTO n;
    RETURN core.format_document_no(t.prefix, core.fiscal_year_label(y.start_date), t.digits, n);
END;
$$;
COMMENT ON FUNCTION core.next_document_no(text, date) IS
    'بيصرف الرقم الجاي لنوع المستند في سنة تاريخه (بيبدأ من 1 كل سنة) — جوه نفس transaction الحفظ';
