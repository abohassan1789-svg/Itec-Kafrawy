-- 0033: مستند «الأرصدة الافتتاحية» (المرحلة 4 — النموذج 2 «شريط الأقسام + كل الأطراف جاهزة»،
--       اختيار المستخدم 2026-11-17 — mockups/phase4/02_opening_balances.html) + رد محرك الترحيل (ج).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (2026-11-17):
--   * **مستند واحد بس** للبرنامج كله (السجل id = 1) · تاريخه = اللي المستخدم بيكتبه ويعدّله (الافتراضي النهارده).
--     مفيش حساب أرصدة من الأكسس (كل خانات «رصيد أول المدة» هناك أصفار).
--   * كل سطر = حساب فرعي + طرف (لو حساب رقابة) + مدين **أو** دائن · **الفرق لوحده على «34 أرصدة افتتاحية»**
--     (محرك الترحيل — دور «opening» في 0032) — فالحساب ده نفسه مايتكتبش عليه سطر.
--   * الافتراضي حسابات الميزانية بس، وتشيك بوكس «كل الحسابات» في الشاشة بيفتح الإيرادات والمصروفات · بالجنيه.
--   * «حفظ مسودة» + «ترحيل» + «إلغاء الترحيل» بالصلاحية بس (زي القيد اليدوي) — المرحّل سطوره مقفولة.
--   * **خانة «رصيد أول المدة» في شاشتي العملاء والموردين بتعدّل نفس السطر** — ومايتكررش: الطرف ليه سطر واحد
--     على نفس الحساب (UNIQUE ... NULLS NOT DISTINCT).
--   * المخزون مش هنا (بضاعة أول المدة — المرحلة 6).
--   * رد (ج) على محرك الترحيل: **شيل «لازم مركز تكلفة» من المبيعات والخدمات والمردودات والتكلفة**.

-- ---------------------------------------------------------------------------
-- 0) رد (ج): مركز التكلفة اختياري على المبيعات والخدمات والمردودات والتكلفة
-- ---------------------------------------------------------------------------
UPDATE acc.accounts SET requires_cost_center = false
 WHERE code IN ('4101', '4102', '42', '51') AND requires_cost_center;

-- ---------------------------------------------------------------------------
-- 1) رأس المستند (سجل واحد بس)
-- ---------------------------------------------------------------------------
CREATE TABLE acc.opening_balances (
    id            smallint      PRIMARY KEY DEFAULT 1,
    balance_date  date          NOT NULL DEFAULT current_date,
    status        varchar(10)   NOT NULL DEFAULT 'draft',
    notes         varchar(500),
    updated_at    timestamptz   NOT NULL DEFAULT now(),
    updated_by    integer       REFERENCES sec.app_users (id) ON DELETE SET NULL,
    posted_at     timestamptz,
    posted_by     integer       REFERENCES sec.app_users (id) ON DELETE SET NULL,
    row_version   integer       NOT NULL DEFAULT 1 CONSTRAINT opening_balances_row_version_chk CHECK (row_version > 0),
    CONSTRAINT opening_balances_one_chk CHECK (id = 1),
    CONSTRAINT opening_balances_status_chk CHECK (status IN ('draft', 'posted')),
    CONSTRAINT opening_balances_posted_chk CHECK ((status = 'posted') = (posted_at IS NOT NULL))
);
CREATE INDEX opening_balances_updated_by_idx ON acc.opening_balances (updated_by);
CREATE INDEX opening_balances_posted_by_idx ON acc.opening_balances (posted_by);
COMMENT ON TABLE acc.opening_balances IS
    'مستند الأرصدة الافتتاحية (واحد بس للبرنامج): التاريخ · مسودة / مرحّل — الترحيل بقيد أوتوماتيك OB';
INSERT INTO acc.opening_balances (id) VALUES (1);

CREATE TRIGGER zz_row_version_trg BEFORE UPDATE ON acc.opening_balances
    FOR EACH ROW EXECUTE FUNCTION core.bump_row_version();

CREATE FUNCTION acc.opening_balances_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'مستند الأرصدة الافتتاحية مايتمسحش.';
    END IF;
    IF NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'مستند الأرصدة الافتتاحية واحد بس.';
    END IF;
    IF OLD.status = 'posted' AND NEW.status = 'posted'
       AND (NEW.balance_date, NEW.notes) IS DISTINCT FROM (OLD.balance_date, OLD.notes) THEN
        RAISE EXCEPTION 'مستند الأرصدة الافتتاحية مرحّل — الغي ترحيله الأول عشان تعدّله.';
    END IF;
    IF NEW.status = 'draft' THEN
        NEW.posted_at := NULL;
        NEW.posted_by := NULL;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER opening_balances_guard_trg BEFORE UPDATE OR DELETE ON acc.opening_balances
    FOR EACH ROW EXECUTE FUNCTION acc.opening_balances_guard();

-- ---------------------------------------------------------------------------
-- 2) السطور: حساب + طرف + مدين أو دائن
-- ---------------------------------------------------------------------------
CREATE TABLE acc.opening_balance_lines (
    id              bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      integer        NOT NULL REFERENCES acc.accounts (id) ON DELETE RESTRICT,
    customer_id     integer        REFERENCES party.customers (id) ON DELETE CASCADE,
    supplier_id     integer        REFERENCES party.suppliers (id) ON DELETE CASCADE,
    employee_id     integer        REFERENCES party.employees (id) ON DELETE CASCADE,
    partner_id      integer        REFERENCES party.partners (id) ON DELETE CASCADE,
    debit           numeric(18,2)  NOT NULL DEFAULT 0,
    credit          numeric(18,2)  NOT NULL DEFAULT 0,
    cost_center_id  integer        REFERENCES acc.cost_centers (id) ON DELETE RESTRICT,
    description     varchar(300),
    updated_at      timestamptz    NOT NULL DEFAULT now(),
    updated_by      integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    CONSTRAINT opening_lines_amount_chk CHECK (debit >= 0 AND credit >= 0 AND (debit > 0) <> (credit > 0)),
    CONSTRAINT opening_lines_one_party_chk CHECK (num_nonnulls(customer_id, supplier_id, employee_id, partner_id) <= 1),
    -- مايتكررش: الطرف (أو الحساب من غير طرف) ليه سطر واحد بس على نفس الحساب.
    CONSTRAINT opening_lines_unique UNIQUE NULLS NOT DISTINCT (account_id, customer_id, supplier_id, employee_id, partner_id)
);
CREATE INDEX opening_lines_account_idx ON acc.opening_balance_lines (account_id);
CREATE INDEX opening_lines_customer_idx ON acc.opening_balance_lines (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX opening_lines_supplier_idx ON acc.opening_balance_lines (supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX opening_lines_employee_idx ON acc.opening_balance_lines (employee_id) WHERE employee_id IS NOT NULL;
CREATE INDEX opening_lines_partner_idx ON acc.opening_balance_lines (partner_id) WHERE partner_id IS NOT NULL;
CREATE INDEX opening_lines_cost_center_idx ON acc.opening_balance_lines (cost_center_id) WHERE cost_center_id IS NOT NULL;
CREATE INDEX opening_lines_updated_by_idx ON acc.opening_balance_lines (updated_by);
COMMENT ON TABLE acc.opening_balance_lines IS
    'سطور الأرصدة الافتتاحية: حساب فرعي + طرف (لو رقابة) + مدين أو دائن — الفرق بيروح لوحده على 34 مع الترحيل';

CREATE FUNCTION acc.opening_lines_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    h        acc.opening_balances%ROWTYPE;
    a        acc.accounts%ROWTYPE;
    cc       acc.cost_centers%ROWTYPE;
    l        acc.opening_balance_lines%ROWTYPE;
    kinds    constant text[] := ARRAY['customer', 'supplier', 'employee', 'partner'];
    labels   constant text[] := ARRAY['العميل', 'المورد', 'الموظف', 'الشريك'];
    ids      integer[];
    i        integer;
    moving   boolean := coalesce(current_setting('acc.opening_line_move', true), '') = 'on';
BEGIN
    -- التفريع (acc.split_account): نقل الحساب للـ «— عام» بس (حتى لو المستند مرحّل).
    IF TG_OP = 'UPDATE' AND moving THEN
        IF (to_jsonb(NEW) - 'account_id') IS DISTINCT FROM (to_jsonb(OLD) - 'account_id') THEN
            RAISE EXCEPTION 'نقل سطور الأرصدة الافتتاحية مع التفريع بيغيّر الحساب بس.';
        END IF;
        RETURN NEW;
    END IF;
    -- FOR SHARE: محدش يرحّل المستند واحنا بنعدّل سطوره.
    SELECT * INTO h FROM acc.opening_balances WHERE id = 1 FOR SHARE;
    IF h.status = 'posted' THEN
        RAISE EXCEPTION 'مستند الأرصدة الافتتاحية مرحّل — الغي ترحيله الأول عشان تعدّل الأرصدة.';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    l := NEW;
    SELECT * INTO a FROM acc.accounts WHERE id = l.account_id FOR SHARE;
    IF NOT a.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» رئيسي — الرصيد على الحسابات الفرعية بس.', a.code, a.name_ar;
    END IF;
    IF NOT a.is_active AND (TG_OP = 'INSERT' OR OLD.account_id IS DISTINCT FROM l.account_id) THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — اختار حساب نشط.', a.code, a.name_ar;
    END IF;
    IF EXISTS (SELECT 1 FROM acc.default_accounts WHERE key = 'opening' AND account_id = l.account_id) THEN
        RAISE EXCEPTION 'الحساب «% %» هو حساب الأرصدة الافتتاحية — الفرق بيتحسب عليه لوحده مع الترحيل.',
            a.code, a.name_ar;
    END IF;
    -- الطرف: إجباري لو الحساب رقابة ومن نوعه بس · ممنوع لو الحساب مالوش طرف.
    ids := ARRAY[l.customer_id, l.supplier_id, l.employee_id, l.partner_id];
    FOR i IN 1..4 LOOP
        IF ids[i] IS NOT NULL AND kinds[i] IS DISTINCT FROM a.party_kind THEN
            IF a.party_kind IS NULL THEN
                RAISE EXCEPTION 'الحساب «% %» مالوش طرف — شيل % من السطر.', a.code, a.name_ar, labels[i];
            END IF;
            RAISE EXCEPTION 'الحساب «% %» طرفه % — مش %.', a.code, a.name_ar,
                labels[array_position(kinds, a.party_kind)], labels[i];
        END IF;
    END LOOP;
    IF a.party_kind IS NOT NULL AND ids[array_position(kinds, a.party_kind)] IS NULL THEN
        RAISE EXCEPTION 'الحساب «% %» حساب رقابة — لازم تختار %.', a.code, a.name_ar,
            labels[array_position(kinds, a.party_kind)];
    END IF;
    -- مركز التكلفة: اختياري — إلا لو الحساب «لازم مركز تكلفة».
    IF l.cost_center_id IS NULL THEN
        IF a.requires_cost_center THEN
            RAISE EXCEPTION 'الحساب «% %» لازم يتكتب عليه مركز تكلفة.', a.code, a.name_ar;
        END IF;
    ELSIF TG_OP = 'INSERT' OR OLD.cost_center_id IS DISTINCT FROM l.cost_center_id THEN
        SELECT * INTO cc FROM acc.cost_centers WHERE id = l.cost_center_id FOR SHARE;
        IF NOT cc.is_leaf OR NOT cc.is_active THEN
            RAISE EXCEPTION 'مركز التكلفة «% %» رئيسي أو موقوف — اختار مركز فرعي نشط.', cc.code, cc.name_ar;
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER opening_lines_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON acc.opening_balance_lines
    FOR EACH ROW EXECUTE FUNCTION acc.opening_lines_guard();

-- ---------------------------------------------------------------------------
-- 3) التفريع: سطور الأرصدة الافتتاحية بتنزل على «— عام» زي سطور القيود (نفس دالة 0031 + سطر).
-- ⚠️ أي سكريبت جاي بيعدّل التفريع يعدّل `acc.split_account_core` — والدالة دي غلاف بس.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acc.split_account(p_account_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم الحساب «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    v_general integer;
BEGIN
    v_general := acc.split_account_core(p_account_id, p_general_code);
    PERFORM set_config('acc.journal_line_move', 'on', true);
    UPDATE acc.journal_lines SET account_id = v_general WHERE account_id = p_account_id;
    PERFORM set_config('acc.journal_line_move', '', true);
    PERFORM set_config('acc.opening_line_move', 'on', true);
    UPDATE acc.opening_balance_lines SET account_id = v_general WHERE account_id = p_account_id;
    PERFORM set_config('acc.opening_line_move', '', true);
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_account(integer, varchar) IS
    'تفريع حساب فرعي: split_account_core + سطور القيود والأرصدة الافتتاحية بتنزل على «— عام»';
