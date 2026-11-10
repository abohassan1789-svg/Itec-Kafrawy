-- 0005: العملات وأسعار الصرف (المرحلة 1ب — شاشة «العملات وأسعار الصرف»، النموذج 10)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (docs/03 القسم 9.5): تعدد عملات كامل · الجنيه المصري EGP عملة أساسية واحدة بس.
-- القاعدة نفسها بتحمي:
--   * عملة أساسية واحدة بس (Partial Unique Index) · الأساسية دايماً نشطة ومابتتمسحش ولا تتغيّر.
--   * الكود 3 حروف إنجليزي كابيتال، مش متكرر، ومابيتغيّرش بعد الإضافة.
--   * الكسور من 0 لـ 6.
--   * أسعار الصرف **بالتاريخ**: سعر واحد لكل عملة في اليوم، أكبر من صفر، ومفيش سعر للعملة الأساسية (= 1).
-- السعر اللي بياخده أي مستند = آخر سعر في تاريخه أو قبله (core.rate_on).

CREATE TABLE core.currencies (
    id             integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code           char(3)      NOT NULL,
    name_ar        varchar(60)  NOT NULL,
    name_en        varchar(60),
    symbol         varchar(10),
    decimals       smallint     NOT NULL DEFAULT 2,
    fraction_name  varchar(30),                              -- اسم الكسر للتفقيط (قرش، سنت…)
    is_base        boolean      NOT NULL DEFAULT false,
    is_active      boolean      NOT NULL DEFAULT true,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT currencies_code_unique UNIQUE (code),
    CONSTRAINT currencies_code_format CHECK (code ~ '^[A-Z]{3}$'),
    CONSTRAINT currencies_name_not_blank CHECK (btrim(name_ar) <> ''),
    CONSTRAINT currencies_decimals_range CHECK (decimals BETWEEN 0 AND 6),
    CONSTRAINT currencies_base_active CHECK (NOT is_base OR is_active)
);
CREATE UNIQUE INDEX currencies_one_base ON core.currencies ((true)) WHERE is_base;
COMMENT ON TABLE core.currencies IS 'العملات — عملة أساسية واحدة بس (EGP)';

INSERT INTO core.currencies (code, name_ar, name_en, symbol, decimals, fraction_name, is_base) VALUES
    ('EGP', 'جنيه مصري', 'Egyptian Pound', 'ج.م', 2, 'قرش', true),
    ('USD', 'دولار أمريكي', 'US Dollar', '$', 2, 'سنت', false),
    ('EUR', 'يورو', 'Euro', '€', 2, 'سنت', false);

-- العملة الأساسية في بيانات الشركة لازم تبقى عملة موجودة.
ALTER TABLE core.company_settings
    ADD CONSTRAINT company_settings_base_currency_fk FOREIGN KEY (base_currency_code) REFERENCES core.currencies (code);

-- الكود ثابت بعد الإضافة · الأساسية مابتتمسحش ولا بتتغيّر لعملة عادية (ولا عملة تبقى أساسية من البرنامج).
CREATE FUNCTION core.currencies_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_base THEN
            RAISE EXCEPTION 'العملة الأساسية % مينفعش تتمسح.', OLD.code;
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.code <> OLD.code THEN
        RAISE EXCEPTION 'كود العملة % مينفعش يتغيّر بعد الإضافة.', OLD.code;
    END IF;
    IF NEW.is_base <> OLD.is_base THEN
        RAISE EXCEPTION 'العملة الأساسية ثابتة (EGP) — مينفعش تتغيّر.';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER currencies_guard_trg
    BEFORE UPDATE OR DELETE ON core.currencies
    FOR EACH ROW EXECUTE FUNCTION core.currencies_guard();

-- ---------------------------------------------------------------------------
-- أسعار الصرف بالتاريخ: 1 وحدة من العملة = rate جنيه.
-- ---------------------------------------------------------------------------
CREATE TABLE core.exchange_rates (
    id           integer       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    currency_id  integer       NOT NULL REFERENCES core.currencies (id) ON DELETE CASCADE,
    rate_date    date          NOT NULL,
    rate         numeric(18,6) NOT NULL,
    created_by   integer       REFERENCES sec.app_users (id) ON DELETE SET NULL,
    created_at   timestamptz   NOT NULL DEFAULT now(),
    updated_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT exchange_rates_one_per_day UNIQUE (currency_id, rate_date),
    CONSTRAINT exchange_rates_positive CHECK (rate > 0)
);
COMMENT ON TABLE core.exchange_rates IS 'أسعار الصرف بالتاريخ (سعر واحد لكل عملة في اليوم، بالجنيه)';

CREATE FUNCTION core.exchange_rates_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT is_base FROM core.currencies WHERE id = NEW.currency_id) THEN
        RAISE EXCEPTION 'العملة الأساسية سعرها دايماً 1 — مالهاش أسعار صرف.';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER exchange_rates_guard_trg
    BEFORE INSERT OR UPDATE ON core.exchange_rates
    FOR EACH ROW EXECUTE FUNCTION core.exchange_rates_guard();

-- السعر في تاريخ معيّن = آخر سعر في اليوم ده أو قبله (الأساسية = 1، ومفيش سعر = NULL).
CREATE FUNCTION core.rate_on(p_currency_code char(3), p_date date) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN c.is_base THEN 1::numeric
                ELSE (SELECT r.rate FROM core.exchange_rates r
                      WHERE r.currency_id = c.id AND r.rate_date <= p_date
                      ORDER BY r.rate_date DESC LIMIT 1) END
    FROM core.currencies c
    WHERE c.code = p_currency_code;
$$;
COMMENT ON FUNCTION core.rate_on(char, date) IS 'سعر العملة بالجنيه في تاريخ = آخر سعر في اليوم ده أو قبله';
