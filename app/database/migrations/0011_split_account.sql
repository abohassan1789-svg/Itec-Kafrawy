-- 0011: تفريع أي حساب فرعي (طلب المستخدم 2026-11-08 — زرار «+ فرعي تحت المحدد» في شجرة الحسابات).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قبل كده: الفرعي (is_leaf) مينفعش يبقى تحته حسابات، والحساب النظامي مينفعش يتحوّل رئيسي خالص،
-- والمربوط بكود ضريبة مينفعش يتحوّل رئيسي. دلوقتي فيه **عملية واحدة مضبوطة** «تفريع الحساب»
-- (acc.split_account) بتعمل كل ده في نفس الـ transaction، والقواعد العادية بتفضل زي ما هي لأي تعديل تاني:
--
--   1. الحساب بيتقفل (FOR UPDATE) — جهاز تاني بيعدّله أو بيفرّعه في نفس اللحظة بيستنى، وبعدين بيترفض
--      لأن الإصدار (row_version) زاد.
--   2. الحساب بيتحوّل **رئيسي** (الكود والمكان والدور والنظامي بيفضلوا زي ما هم — الأب بيفضل نظامي بدوره
--      زي «النقدية» و«البنوك» في الدليل، عشان أي حساب جديد تحته يورث الدور).
--   3. **مع كل تفريع** (قرار المستخدم 2026-11-08: «عام» لكل تفريع) البرنامج بيعمل تحته أول حساب لوحده
--      «<الاسم> — عام» بالكود المقترح (5402 ← 540201 مصاريف بنكية — عام)، وبياخد: الدور + النظامي + ربط أكواد
--      الضريبة + الطرف ومركز التكلفة والعملة والتصنيف (والقيود — المرحلة 4). كده المستندات والقيود القديمة
--      تفضل عارفة تقيّد فين، والحساب اللي المستخدم بيكتبه بياخد الكود اللي بعده (540202).
--   4. الحساب الجديد اللي المستخدم كتبه بيتضاف بعد كده (من الـ Service) في نفس الـ transaction.
--
-- الاستثناء الوحيد من القواعد: «النظامي مايتحوّلش رئيسي» — بيتعدّى **جوه الدالة دي بس** وللحساب ده بس
-- (إعداد محلي للـ transaction: acc.split_account_id = رقم الحساب). ربط الضريبة مش محتاج استثناء:
-- بيتفك الأول وبيترجع على «العام».

-- ---------------------------------------------------------------------------
-- الحماية (إضافة / تعديل) — نفس 0009 بالظبط + استثناء التفريع
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION acc.accounts_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    p          acc.accounts%ROWTYPE;
    has_kids   boolean := false;
    role_type  varchar(10);
    splitting  boolean := false;
BEGIN
    IF NEW.parent_id IS NULL THEN
        NEW.level := 1;
        IF NEW.account_type IS NULL THEN
            RAISE EXCEPTION 'الحساب الجذر «%» لازم يتحدد نوعه.', NEW.code;
        END IF;
    ELSE
        -- FOR SHARE: لو جهاز تاني بيحوّل الأب فرعي أو بيوقفه في نفس اللحظة، واحد يستنى التاني.
        SELECT * INTO p FROM acc.accounts WHERE id = NEW.parent_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'الحساب الأب مش موجود (ممكن يكون اتمسح).';
        END IF;
        IF p.id = NEW.id THEN
            RAISE EXCEPTION 'الحساب مينفعش يبقى تحت نفسه.';
        END IF;
        IF p.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» فرعي — مينفعش يبقى تحته حسابات (فرّع منه بـ «+ فرعي تحت المحدد»).',
                p.code, p.name_ar;
        END IF;
        -- تحت أب موقوف: مينفعش تضيف ولا تنقل ولا تنشّط (تعديل حساب موقوف تحت أب موقوف عادي).
        IF NOT p.is_active AND (TG_OP = 'INSERT' OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                                OR (NEW.is_active AND NOT OLD.is_active)) THEN
            RAISE EXCEPTION 'الحساب «% %» موقوف — مينفعش يبقى تحته حسابات نشطة أو جديدة.', p.code, p.name_ar;
        END IF;
        IF left(NEW.code, length(p.code)) <> p.code OR length(NEW.code) <= length(p.code) THEN
            RAISE EXCEPTION 'كود الحساب لازم يبدأ بكود الأب «%» ويبقى أطول منه.', p.code;
        END IF;
        NEW.level := p.level + 1;
        IF NEW.level > 5 THEN
            RAISE EXCEPTION 'أقصى عدد مستويات في الدليل 5 — مينفعش تضيف تحت «%».', p.code;
        END IF;
        NEW.account_type := p.account_type;
    END IF;
    IF NEW.level = 5 AND NOT NEW.is_leaf THEN
        RAISE EXCEPTION 'المستوى الخامس آخر مستوى — الحساب «%» لازم يبقى فرعي.', NEW.code;
    END IF;
    NEW.statement := CASE WHEN NEW.account_type IN ('asset', 'liability', 'equity') THEN 'bs' ELSE 'is' END;

    IF NEW.role_code IS NOT NULL THEN
        SELECT account_type INTO role_type FROM acc.account_roles WHERE code = NEW.role_code;
        IF role_type IS NOT NULL AND role_type <> NEW.account_type THEN
            RAISE EXCEPTION 'الدور ده مايتحطش على حساب من النوع ده (الحساب «%»).', NEW.code;
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        -- التفريع (acc.split_account بس): فرعي ← رئيسي للحساب ده بس، من غير أي تغيير تاني.
        splitting := coalesce(current_setting('acc.split_account_id', true), '') = OLD.id::text
                     AND OLD.is_leaf AND NOT NEW.is_leaf;
        has_kids := EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = OLD.id);
        IF OLD.is_system AND (NEW.code <> OLD.code OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                              OR NEW.role_code IS DISTINCT FROM OLD.role_code
                              OR (NEW.is_leaf <> OLD.is_leaf AND NOT splitting)
                              OR NEW.account_type <> OLD.account_type OR NOT NEW.is_system) THEN
            RAISE EXCEPTION 'الحساب «% %» نظامي — كوده ومكانه ودوره ونوعه ثابتين.', OLD.code, OLD.name_ar;
        END IF;
        IF OLD.is_system AND NOT NEW.is_active THEN
            RAISE EXCEPTION 'الحساب «% %» نظامي — مايتوقفش (البرنامج محتاجه).', OLD.code, OLD.name_ar;
        END IF;
        IF NEW.is_system AND NOT OLD.is_system THEN
            RAISE EXCEPTION 'مينفعش تخلّي حساب نظامي بإيدك.';
        END IF;
        IF has_kids AND (NEW.code <> OLD.code OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
                         OR NEW.account_type <> OLD.account_type) THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات — مينفعش تغيّر كوده ولا مكانه ولا نوعه.', OLD.code, OLD.name_ar;
        END IF;
        IF has_kids AND NEW.is_leaf THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات — مينفعش يتحوّل فرعي.', OLD.code, OLD.name_ar;
        END IF;
        IF OLD.is_active AND NOT NEW.is_active
           AND EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = OLD.id AND is_active) THEN
            RAISE EXCEPTION 'الحساب «% %» تحته حسابات نشطة — وقّفهم الأول.', OLD.code, OLD.name_ar;
        END IF;
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------
-- تفريع الحساب
-- ---------------------------------------------------------------------------
CREATE FUNCTION acc.split_account(p_account_id integer, p_general_code varchar DEFAULT NULL)
RETURNS integer      -- رقم الحساب «— عام» اللي اتعمل
LANGUAGE plpgsql AS $$
DECLARE
    p              acc.accounts%ROWTYPE;
    v_sales        integer[];
    v_purchase     integer[];
    v_general      integer;
    v_code         varchar(20);
    v_suffix       constant text := ' — عام';
BEGIN
    -- 1) القفل: أي جهاز تاني بيعدّل / بيفرّع / بيربط ضريبة على الحساب ده بيستنى لحد ما نخلص.
    SELECT * INTO p FROM acc.accounts WHERE id = p_account_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'الحساب ده مش موجود (ممكن يكون اتمسح).';
    END IF;
    IF NOT p.is_leaf THEN
        RAISE EXCEPTION 'الحساب «% %» رئيسي أصلاً — ضيف تحته على طول.', p.code, p.name_ar;
    END IF;
    IF p.level >= 5 THEN
        RAISE EXCEPTION 'الحساب «% %» في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.', p.code, p.name_ar;
    END IF;
    IF NOT p.is_active THEN
        RAISE EXCEPTION 'الحساب «% %» موقوف — نشّطه الأول وبعدين فرّع منه.', p.code, p.name_ar;
    END IF;

    SELECT array_agg(id) FILTER (WHERE sales_account_id = p.id),
           array_agg(id) FILTER (WHERE purchase_account_id = p.id)
      INTO v_sales, v_purchase
      FROM acc.tax_codes WHERE p.id IN (sales_account_id, purchase_account_id);

    -- 2) فك ربط الضريبة مؤقتاً (بيرجع على «العام» تحت) — من غير استثناء في قاعدة الضريبة.
    UPDATE acc.tax_codes SET sales_account_id = NULL WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = NULL WHERE id = ANY (v_purchase);

    -- 3) الحساب يتحوّل رئيسي (الاستثناء للحساب ده بس، وبيتقفل تاني على طول).
    PERFORM set_config('acc.split_account_id', p.id::text, true);
    UPDATE acc.accounts SET is_leaf = false WHERE id = p.id;
    PERFORM set_config('acc.split_account_id', '', true);

    -- 4) أول حساب تحته «— عام» (مع كل تفريع) بياخد الدور والنظامي والربط وكل اللي بيتورث.
    v_code := coalesce(nullif(btrim(p_general_code), ''),
                       p.code || CASE WHEN p.level <= 1 THEN '1' ELSE '01' END);
    INSERT INTO acc.accounts (code, name_ar, name_en, parent_id, is_leaf, account_type, nature, classification,
                              cash_flow, role_code, party_kind, requires_cost_center, currency_code,
                              is_active, is_system)
    VALUES (v_code, left(p.name_ar, 120 - length(v_suffix)) || v_suffix,
            CASE WHEN p.name_en IS NULL THEN NULL ELSE left(p.name_en, 110) || ' — General' END,
            p.id, true, p.account_type, p.nature, p.classification, p.cash_flow, p.role_code, p.party_kind,
            p.requires_cost_center, p.currency_code, true, p.is_system)
    RETURNING id INTO v_general;

    -- 5) TODO المرحلة 4 (القيود) — قرار المستخدم (أ): نقل الحركة القديمة للعام في نفس الـ transaction:
    --    UPDATE acc.journal_lines SET account_id = v_general WHERE account_id = p.id;
    --    (سؤال مفتوح: الأرصدة المجمّعة لو اتعملت جدول لوحدها بتتنقل هنا كمان.)

    -- 6) ربط أكواد الضريبة يرجع على العام.
    UPDATE acc.tax_codes SET sales_account_id = v_general WHERE id = ANY (v_sales);
    UPDATE acc.tax_codes SET purchase_account_id = v_general WHERE id = ANY (v_purchase);
    RETURN v_general;
END $$;
COMMENT ON FUNCTION acc.split_account(integer, varchar) IS
    'تفريع حساب فرعي: يتحوّل رئيسي + «<الاسم> — عام» (مع كل تفريع) بالدور والنظامي وربط الضريبة (والقيود — المرحلة 4)';
