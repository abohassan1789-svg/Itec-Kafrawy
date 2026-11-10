-- 0016: المشاريع / أوامر التوريد (المرحلة 3أ — شاشة «أوامر التوريد»، النموذج 1 اختيار المستخدم 2026-11-10).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- الفكرة (mockups/phase2/04_projects.html + docs/03 الفكرة 13): أمر التوريد = مشروع لعميل، و«بُعد» على الفواتير
-- والمصروفات وسطور القيود عشان تطلع تكلفة وربحية كل أمر (بدل CostAmr / TBAMR في الأكسس).
--
-- ردود المستخدم (2026-11-10):
--   1) **العميل اختياري** في الأمر (لو اتختار لازم يبقى نشط).
--   2) **الحالة: جاري / مقفول / ملغي.**
--   3) **رقم الأمر بيطلع لوحده** بالشكل 0001-Itec، و**يتعدّل عادي** (ومايتكررش).
--      الشكل متخزن في acc.project_numbering (pattern + digits) عشان لما المستخدم يبعت الصيغة النهائية
--      تتغيّر بسكريبت صغير من غير ما الكود يتغيّر: {SEQ} = المسلسل بأصفار · {YYYY} / {YY} = سنة تاريخ الأمر.
--   4) **شامل / غير شامل · فاتورة / بيان** على الأمر = **افتراضي** بيتنقل للفاتورة ويتغيّر فيها.
--   5) **العملة** على الأمر = **افتراضي** للفاتورة ويتغيّر فيها (القيمة بعملة الأمر — مفيش سعر صرف ثابت).
--   6) **تاريخ بداية ونهاية** (اختياريين) جنب تاريخ الأمر.
-- القواعد (من النماذج): مركز التكلفة **فرعي نشط** ومطلوب · القيمة ≥ 0 (الصفر مسموح لحد ما تتحدد) ·
-- الأمر اللي عليه حركة (فواتير / مصروفات / قيود) مايتمسحش — FK RESTRICT من الجداول دي لما تتعمل.

-- ---------------------------------------------------------------------------
-- شكل الترقيم (سجل واحد) — بيتغيّر بسكريبت لما المستخدم يبعت الصيغة
-- ---------------------------------------------------------------------------
CREATE TABLE acc.project_numbering (
    id          smallint     PRIMARY KEY DEFAULT 1,
    pattern     varchar(40)  NOT NULL DEFAULT '{SEQ}-Itec',
    digits      smallint     NOT NULL DEFAULT 4,
    last_no     integer      NOT NULL DEFAULT 0,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT project_numbering_single CHECK (id = 1),
    CONSTRAINT project_numbering_pattern_chk CHECK (position('{SEQ}' IN pattern) > 0),
    CONSTRAINT project_numbering_digits_range CHECK (digits BETWEEN 1 AND 8),
    CONSTRAINT project_numbering_last_no_chk CHECK (last_no >= 0)
);
COMMENT ON TABLE acc.project_numbering IS
    'شكل رقم أمر التوريد ({SEQ} = المسلسل · {YYYY}/{YY} = السنة) وآخر مسلسل اتصرف — سجل واحد';
INSERT INTO acc.project_numbering (id) VALUES (1);

-- العدّاد مابينزلش (إلا لو الشكل نفسه اتغيّر) ومابيتمسحش.
CREATE FUNCTION acc.project_numbering_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'إعداد ترقيم أوامر التوريد مابيتمسحش.';
    END IF;
    IF NEW.last_no < OLD.last_no AND NEW.pattern = OLD.pattern THEN
        RAISE EXCEPTION 'عدّاد أوامر التوريد مابينزلش (من % لـ %) — عشان الأرقام ماتتكررش.', OLD.last_no, NEW.last_no;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER project_numbering_guard_trg
    BEFORE UPDATE OR DELETE ON acc.project_numbering
    FOR EACH ROW EXECUTE FUNCTION acc.project_numbering_guard();

-- ---------------------------------------------------------------------------
-- أوامر التوريد
-- ---------------------------------------------------------------------------
CREATE TABLE acc.projects (
    id              integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(30)   NOT NULL,                       -- رقم الأمر (بيطلع لوحده ويتعدّل)
    name            varchar(200)  NOT NULL,                       -- اسم المشروع
    customer_id     integer       REFERENCES party.customers (id) ON DELETE RESTRICT,   -- اختياري
    cost_center_id  integer       NOT NULL REFERENCES acc.cost_centers (id) ON DELETE RESTRICT,
    amount          numeric(18,2) NOT NULL DEFAULT 0,             -- قيمة الأمر بعملته (0 = لسه ماتحددتش)
    currency_code   char(3)       NOT NULL REFERENCES core.currencies (code) ON DELETE RESTRICT,
    tax_inclusive   boolean       NOT NULL DEFAULT true,          -- شامل / غير شامل (افتراضي للفاتورة)
    doc_kind        varchar(10)   NOT NULL DEFAULT 'invoice',     -- invoice فاتورة / statement بيان (افتراضي)
    order_date      date          NOT NULL DEFAULT current_date,
    start_date      date,
    end_date        date,
    status          varchar(10)   NOT NULL DEFAULT 'open',        -- open جاري · closed مقفول · cancelled ملغي
    notes           varchar(500),
    created_at      timestamptz   NOT NULL DEFAULT now(),
    updated_at      timestamptz   NOT NULL DEFAULT now(),
    row_version     integer       NOT NULL DEFAULT 1 CONSTRAINT projects_row_version_chk CHECK (row_version > 0),
    CONSTRAINT projects_code_not_blank CHECK (btrim(code) <> '' AND code = btrim(code)),
    CONSTRAINT projects_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT projects_amount_chk CHECK (amount >= 0),
    CONSTRAINT projects_doc_kind_chk CHECK (doc_kind IN ('invoice', 'statement')),
    CONSTRAINT projects_status_chk CHECK (status IN ('open', 'closed', 'cancelled')),
    CONSTRAINT projects_dates_chk CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE UNIQUE INDEX projects_code_unique ON acc.projects (lower(code));
CREATE INDEX projects_customer_idx ON acc.projects (customer_id);
CREATE INDEX projects_cost_center_idx ON acc.projects (cost_center_id);
CREATE INDEX projects_currency_idx ON acc.projects (currency_code);
CREATE INDEX projects_status_date_idx ON acc.projects (status, order_date DESC);
CREATE INDEX projects_date_idx ON acc.projects (order_date DESC, id DESC);
COMMENT ON TABLE acc.projects IS
    'أوامر التوريد (المشاريع): بُعد على الفواتير والمصروفات والقيود — مركز التكلفة فرعي نشط · العميل اختياري';

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل)
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.projects_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    cu  party.customers%ROWTYPE;
    cc  acc.cost_centers%ROWTYPE;
    c   core.currencies%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        NEW.updated_at := now();
    END IF;

    -- العميل (اختياري): لو اتختار لازم يبقى نشط. FOR SHARE: لو جهاز تاني بيوقفه في نفس اللحظة يستنى.
    IF NEW.customer_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.customer_id IS DISTINCT FROM OLD.customer_id) THEN
        SELECT * INTO cu FROM party.customers WHERE id = NEW.customer_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'العميل مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NOT cu.is_active THEN
            RAISE EXCEPTION 'العميل «%» موقوف — مينفعش تختاره لأمر توريد.', cu.name_ar;
        END IF;
    END IF;

    -- مركز التكلفة: فرعي نشط.
    IF TG_OP = 'INSERT' OR NEW.cost_center_id IS DISTINCT FROM OLD.cost_center_id THEN
        SELECT * INTO cc FROM acc.cost_centers WHERE id = NEW.cost_center_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'مركز التكلفة مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF NOT cc.is_leaf THEN
            RAISE EXCEPTION 'مركز التكلفة «% %» رئيسي — اختار مركز فرعي تحته.', cc.code, cc.name_ar;
        END IF;
        IF NOT cc.is_active THEN
            RAISE EXCEPTION 'مركز التكلفة «% %» موقوف — مينفعش تختاره لأمر توريد.', cc.code, cc.name_ar;
        END IF;
    END IF;

    -- العملة: نشطة.
    IF TG_OP = 'INSERT' OR NEW.currency_code IS DISTINCT FROM OLD.currency_code THEN
        SELECT * INTO c FROM core.currencies WHERE code = NEW.currency_code FOR SHARE;
        IF NOT FOUND OR NOT c.is_active THEN
            RAISE EXCEPTION 'العملة «%» مش موجودة أو موقوفة.', NEW.currency_code;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER projects_guard_trg
    BEFORE INSERT OR UPDATE ON acc.projects
    FOR EACH ROW EXECUTE FUNCTION acc.projects_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.projects
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

-- ---------------------------------------------------------------------------
-- الترقيم: {SEQ} بأصفار على الشمال (ولو عدّى الخانات بيكمّل) · {YYYY} / {YY} من تاريخ الأمر
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.format_project_no(p_pattern text, p_digits integer, p_no integer, p_date date)
RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT replace(replace(replace(p_pattern,
        '{SEQ}', CASE WHEN length(p_no::text) >= p_digits THEN p_no::text ELSE lpad(p_no::text, p_digits, '0') END),
        '{YYYY}', to_char(coalesce(p_date, current_date), 'YYYY')),
        '{YY}', to_char(coalesce(p_date, current_date), 'YY'))
$$;

-- الرقم الجاي للعرض بس (مابيصرفش ولا بيقفل) — بيعدّي الأرقام اللي اتكتبت بالإيد قبل كده.
CREATE FUNCTION acc.peek_project_no(p_date date DEFAULT current_date) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
    s  acc.project_numbering%ROWTYPE;
    n  integer;
    v  text;
BEGIN
    SELECT * INTO s FROM acc.project_numbering WHERE id = 1;
    n := s.last_no;
    LOOP
        n := n + 1;
        v := acc.format_project_no(s.pattern, s.digits, n, p_date);
        EXIT WHEN NOT EXISTS (SELECT 1 FROM acc.projects WHERE lower(code) = lower(v));
    END LOOP;
    RETURN v;
END $$;

-- صرف الرقم الجاي جوه transaction الحفظ: قفل صف العدّاد (FOR UPDATE) ← جهازين مايخدوش نفس الرقم،
-- والرقم المكتوب بالإيد قبل كده بيتعدّى. لو الحفظ فشل الرقم بيرجع.
CREATE FUNCTION acc.next_project_no(p_date date DEFAULT current_date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    s  acc.project_numbering%ROWTYPE;
    n  integer;
    v  text;
BEGIN
    SELECT * INTO s FROM acc.project_numbering WHERE id = 1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'إعداد ترقيم أوامر التوريد مش موجود.';
    END IF;
    n := s.last_no;
    LOOP
        n := n + 1;
        v := acc.format_project_no(s.pattern, s.digits, n, p_date);
        EXIT WHEN NOT EXISTS (SELECT 1 FROM acc.projects WHERE lower(code) = lower(v));
    END LOOP;
    UPDATE acc.project_numbering SET last_no = n WHERE id = 1;
    RETURN v;
END $$;
COMMENT ON FUNCTION acc.next_project_no(date) IS
    'بيصرف رقم أمر التوريد الجاي (0001-Itec…) جوه قفل — بيتنادى في نفس transaction الحفظ';

-- ---------------------------------------------------------------------------
-- العميل اللي عليه أوامر توريد: مايتمسحش (رسالة عربي بدل خطأ الـ FK) — يتوقف بس.
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.customers_projects_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    SELECT count(*) INTO n FROM acc.projects WHERE customer_id = OLD.id;
    IF n > 0 THEN
        RAISE EXCEPTION 'العميل «%» عليه % أمر توريد — وقّفه بدل ما تمسحه.', OLD.name_ar, n;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER customers_zprojects_trg
    BEFORE DELETE ON party.customers
    FOR EACH ROW EXECUTE FUNCTION party.customers_projects_guard();

-- ---------------------------------------------------------------------------
-- مركز التكلفة اللي عليه أوامر: مايتمسحش، ومايتحوّلش رئيسي من «تعديل» (بس بالتفريع — الأوامر بتنزل للعام).
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.cost_centers_projects_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NOT (OLD.is_leaf AND NOT NEW.is_leaf) THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND coalesce(current_setting('acc.split_cost_center_id', true), '') = OLD.id::text THEN
        RETURN NEW;                                              -- التفريع بينقل الأوامر للعام
    END IF;
    SELECT count(*) INTO n FROM acc.projects WHERE cost_center_id = OLD.id;
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'المركز «% %» عليه % أمر توريد — وقّفه بدل ما تمسحه.', OLD.code, OLD.name_ar, n;
    END IF;
    RAISE EXCEPTION 'المركز «% %» عليه % أمر توريد — مينفعش يتحوّل رئيسي من «تعديل»؛ استخدم «+ فرعي تحت المحدد» والأوامر هتنزل على «— عام».',
        OLD.code, OLD.name_ar, n;
END $$;
CREATE TRIGGER cost_centers_zprojects_trg
    BEFORE UPDATE OR DELETE ON acc.cost_centers
    FOR EACH ROW EXECUTE FUNCTION acc.cost_centers_projects_guard();

-- ---------------------------------------------------------------------------
-- التفريع (0013) بقى بينقل أوامر التوريد لـ «— عام» (نقطة الـ TODO اتقفلت) — نفس الكود + الخطوة 4.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acc.split_cost_center(p_center_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم المركز «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    p          acc.cost_centers%ROWTYPE;
    v_general  integer;
    v_code     varchar(20);
    v_suffix   constant text := ' — عام';
BEGIN
    -- 1) القفل: أي جهاز تاني بيعدّل / بيفرّع المركز ده بيستنى لحد ما نخلص.
    SELECT * INTO p FROM acc.cost_centers WHERE id = p_center_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مركز التكلفة ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT p.is_leaf THEN
        RAISE EXCEPTION 'المركز «% %» رئيسي أصلاً — ضيف تحته على طول.', p.code, p.name_ar;
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'المركز «% %» في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.', p.code, p.name_ar;
    END IF;
    IF NOT p.is_active THEN
        RAISE EXCEPTION 'المركز «% %» موقوف — نشّطه الأول وبعدين فرّع منه.', p.code, p.name_ar;
    END IF;

    -- 2) المركز يتحوّل رئيسي (الإعداد المحلي بيسمح بده جوه الدالة بس حتى لو عليه أوامر).
    PERFORM set_config('acc.split_cost_center_id', p.id::text, true);
    UPDATE acc.cost_centers SET is_leaf = false WHERE id = p.id;
    PERFORM set_config('acc.split_cost_center_id', '', true);

    -- 3) أول مركز تحته «— عام» (مع كل تفريع — نفس قرار الدليل) بنفس النوع والمسؤول.
    v_code := coalesce(nullif(btrim(p_general_code), ''),
                       p.code || CASE WHEN p.level <= 1 THEN '1' ELSE '01' END);
    INSERT INTO acc.cost_centers (code, name_ar, name_en, parent_id, is_leaf, center_type, manager_name, is_active)
    VALUES (v_code, left(p.name_ar, 120 - length(v_suffix)) || v_suffix,
            CASE WHEN p.name_en IS NULL THEN NULL ELSE left(p.name_en, 110) || ' — General' END,
            p.id, true, p.center_type, p.manager_name, true)
    RETURNING id INTO v_general;

    -- 4) أوامر التوريد بتنزل على «— عام».
    UPDATE acc.projects SET cost_center_id = v_general WHERE cost_center_id = p.id;
    --    TODO (القيود — المرحلة 4): UPDATE acc.journal_lines SET cost_center_id = v_general WHERE cost_center_id = p.id;
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_cost_center(integer, varchar) IS
    'تفريع مركز فرعي: يتحوّل رئيسي + «<الاسم> — عام» تحته وأوامر التوريد بتنزل له (والقيود لما تتعمل)';
