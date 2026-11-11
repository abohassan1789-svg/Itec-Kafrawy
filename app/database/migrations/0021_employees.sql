-- 0021: الموظفين (المرحلة 3ب — شاشة «الموظفين»، النموذج 1 اختيار المستخدم 2026-11-11).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (mockups/phase3/05_employees.html + ردود المستخدم 2026-11-11):
--   * الموظف «طرف» مش شئون عاملين (مفيش مرتبات ولا حضور): بيتستخدم في العهد (1107 عهد الموظفين ببُعد الموظف —
--     المرحلة 5) وأمين الخزينة وأمين المخزن والمندوب.
--   * **الكود** `EMP-001`, `EMP-002`… بيتولّد لوحده وثابت (زي الموردين): المسلسل في `seq` والكود محسوب منه.
--   * **الرقم القومي** و**تاريخ التعيين** اختياريين · الرقم القومي 14 رقم ومايتكررش لو اتكتب.
--   * **القسم نص حر** (مش مركز تكلفة ولا جدول أقسام): أي قسم جديد بيتكتب بيظهر لوحده في قايمة الاختيار بعد كده.
--   * **الأدوار** (مندوب / أمين خزينة / أمين مخزن / بياخد عهدة) — عشان كل شاشة تعرض اللي يخصها بس.
--   * **مفيش حد للعهدة** · **مفيش ربط بمستخدم البرنامج** (المندوب بيتختار بإيده في فاتورة البيع — اتأجّل).
--   * الشاشة في قسم لوحده «الموظفين» في القايمة (عشان يتضاف فيه حاجات تانية بعدين).
-- لسه: الموظف اللي عليه عهدة أو فاتورة كمندوب أو أمين لخزينة/مخزن مايتمسحش (FK RESTRICT من الجداول دي لما تتعمل)
-- · رصيد العهدة الافتتاحي = سطر في مستند الأرصدة الافتتاحية (المرحلة 4).

CREATE TABLE party.employees (
    id                  integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq                 integer       NOT NULL,
    code                varchar(20)   GENERATED ALWAYS AS
                            ('EMP-' || CASE WHEN seq < 1000 THEN lpad(seq::text, 3, '0') ELSE seq::text END) STORED,
    name_ar             varchar(200)  NOT NULL,
    name_en             varchar(200),
    job_title           varchar(120),
    department          varchar(80),                                   -- نص حر (القايمة = الأقسام المكتوبة قبل كده)
    mobile              varchar(30),
    phone               varchar(30),
    email               varchar(120),
    address             varchar(300),
    national_id         varchar(14),                                   -- اختياري: 14 رقم ومايتكررش
    hire_date           date,                                          -- اختياري
    is_sales_rep        boolean       NOT NULL DEFAULT false,          -- مندوب مبيعات
    is_treasurer        boolean       NOT NULL DEFAULT false,          -- أمين خزينة
    is_storekeeper      boolean       NOT NULL DEFAULT false,          -- أمين مخزن
    takes_custody       boolean       NOT NULL DEFAULT false,          -- بياخد عهدة
    notes               varchar(500),
    is_active           boolean       NOT NULL DEFAULT true,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    row_version         integer       NOT NULL DEFAULT 1 CONSTRAINT employees_row_version_chk CHECK (row_version > 0),
    CONSTRAINT employees_seq_unique UNIQUE (seq),
    CONSTRAINT employees_seq_range CHECK (seq > 0),
    CONSTRAINT employees_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT employees_dept_not_blank CHECK (department IS NULL OR btrim(department) <> ''),
    CONSTRAINT employees_nid_format CHECK (national_id IS NULL OR national_id ~ '^[0-9]{14}$'),
    CONSTRAINT employees_hire_range CHECK (hire_date IS NULL OR hire_date BETWEEN DATE '1950-01-01' AND DATE '2100-12-31')
);
CREATE UNIQUE INDEX employees_code_unique ON party.employees (code);
CREATE UNIQUE INDEX employees_nid_unique ON party.employees (national_id) WHERE national_id IS NOT NULL;
CREATE UNIQUE INDEX employees_name_unique ON party.employees (lower(btrim(name_ar)));   -- اسمين زي بعض ← ميّزهم (اسم الأب)
CREATE INDEX employees_dept_idx ON party.employees (department);
CREATE INDEX employees_mobile_idx ON party.employees (mobile);
CREATE INDEX employees_active_idx ON party.employees (is_active);
COMMENT ON TABLE party.employees IS
    'الموظفين: طرف (بُعد) للعهد والمندوب وأمين الخزينة والمخزن — مش شئون عاملين · الكود EMP-001 · القسم نص حر';

-- ---------------------------------------------------------------------------
-- الحماية (تعديل): الكود ثابت.
-- ---------------------------------------------------------------------------
CREATE FUNCTION party.employees_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.seq <> OLD.seq THEN
        RAISE EXCEPTION 'كود الموظف «%» ثابت — مينفعش يتغيّر.', OLD.code;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER employees_guard_trg
    BEFORE UPDATE ON party.employees
    FOR EACH ROW EXECUTE FUNCTION party.employees_guard();

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON party.employees
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();
