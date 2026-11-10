-- 0004: السنة المالية والفترات (المرحلة 1ب — شاشة «السنة المالية والفترات»، النموذج 1)
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قرارات المستخدم (docs/03 القسم 8):
--   * القفل **سنوي بس**: السنة يا مفتوحة يا مقفولة — مفيش قفل شهري.
--   * الشهور (الفترات) بتتعمل لوحدها من «من/إلى» وهي للتقارير بس.
--   * الترقيم بيبدأ من 1 كل سنة (هيتربط بالسنة في شاشة ترقيم المستندات).
-- القاعدة نفسها بتحمي:
--   * مفيش تداخل بين سنتين (exclusion constraint) ولا فجوة (constraint trigger).
--   * المدة من يوم لحد 24 شهر، و«من» قبل «إلى».
--   * الكود مش متكرر.
--   * السنة المقفولة مابتتعدّلش ولا بتتمسح (غير فتحها).
--   * الفترات مابتتعدّلش بإيد — بتتولّد من السنة بس.
-- منع الترحيل في السنة المقفولة هيتعمل مع القيود (المرحلة 4)، وكمان منع مسح سنة عليها قيود.

-- ---------------------------------------------------------------------------
-- السنين
-- ---------------------------------------------------------------------------
CREATE TABLE core.fiscal_years (
    id           integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    year_code    varchar(20)  NOT NULL,                     -- «السنة» زي 2026 أو 2026/2027
    name_ar      varchar(100),
    start_date   date         NOT NULL,
    end_date     date         NOT NULL,
    status       varchar(10)  NOT NULL DEFAULT 'open',
    closed_at    timestamptz,
    closed_by    integer      REFERENCES sec.app_users (id) ON DELETE SET NULL,
    notes        varchar(500),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT fiscal_years_code_unique UNIQUE (year_code),
    CONSTRAINT fiscal_years_code_not_blank CHECK (btrim(year_code) <> ''),
    CONSTRAINT fiscal_years_status_chk CHECK (status IN ('open', 'closed')),
    CONSTRAINT fiscal_years_closed_info CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
    CONSTRAINT fiscal_years_dates_order CHECK (start_date <= end_date),
    CONSTRAINT fiscal_years_max_24_months CHECK (end_date < (start_date + interval '24 months')::date),
    CONSTRAINT fiscal_years_no_overlap EXCLUDE USING gist (daterange(start_date, end_date, '[]') WITH &&)
);
COMMENT ON TABLE core.fiscal_years IS 'السنين المالية — القفل سنوي بس (مفتوحة / مقفولة)';

-- ---------------------------------------------------------------------------
-- الفترات (شهور السنة — للتقارير بس، من غير قفل)
-- ---------------------------------------------------------------------------
CREATE TABLE core.fiscal_periods (
    id              integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fiscal_year_id  integer     NOT NULL REFERENCES core.fiscal_years (id) ON DELETE CASCADE,
    period_no       smallint    NOT NULL,
    name_ar         varchar(40) NOT NULL,                   -- «يناير 2026»
    start_date      date        NOT NULL,
    end_date        date        NOT NULL,
    CONSTRAINT fiscal_periods_no_unique UNIQUE (fiscal_year_id, period_no),
    CONSTRAINT fiscal_periods_dates_order CHECK (start_date <= end_date),
    CONSTRAINT fiscal_periods_no_range CHECK (period_no BETWEEN 1 AND 25)
);
CREATE INDEX fiscal_periods_dates_idx ON core.fiscal_periods (start_date, end_date);
COMMENT ON TABLE core.fiscal_periods IS 'شهور السنة المالية — بتتولّد لوحدها من السنة (للتقارير بس)';

-- توليد الفترات: شهر شهر من «من» لـ «إلى» (أول وآخر شهر بيتقصّوا على حدود السنة).
CREATE FUNCTION core.fiscal_years_build_periods() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    months_ar constant text[] := ARRAY['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو',
                                       'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.start_date = OLD.start_date AND NEW.end_date = OLD.end_date THEN
        RETURN NULL;
    END IF;
    DELETE FROM core.fiscal_periods WHERE fiscal_year_id = NEW.id;
    INSERT INTO core.fiscal_periods (fiscal_year_id, period_no, name_ar, start_date, end_date)
    SELECT NEW.id,
           row_number() OVER (ORDER BY m),
           months_ar[extract(month FROM m)::int] || ' ' || extract(year FROM m)::int,
           greatest(m::date, NEW.start_date),
           least((m + interval '1 month' - interval '1 day')::date, NEW.end_date)
    FROM generate_series(date_trunc('month', NEW.start_date::timestamp),
                         date_trunc('month', NEW.end_date::timestamp),
                         interval '1 month') AS m;
    RETURN NULL;
END;
$$;
CREATE TRIGGER fiscal_years_periods_trg
    AFTER INSERT OR UPDATE OF start_date, end_date ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_build_periods();

-- الفترات مابتتعدّلش بإيد: أي تغيير لازم ييجي من trigger السنة (أو المسح المتسلسل).
CREATE FUNCTION core.fiscal_periods_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION 'الفترات بتتعمل لوحدها من «من/إلى» في السنة المالية — مينفعش تتعدّل بإيد.';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE TRIGGER fiscal_periods_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON core.fiscal_periods
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_periods_guard();

-- السنة المقفولة: مابتتعدّلش ولا بتتمسح — المسموح بس إنها تتفتح (الحالة + بيانات القفل).
CREATE FUNCTION core.fiscal_years_closed_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'closed' THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة ومينفعش تتمسح.', OLD.year_code;
        END IF;
        IF NEW.status = 'closed' OR
           (NEW.year_code, NEW.name_ar, NEW.start_date, NEW.end_date, NEW.notes)
               IS DISTINCT FROM (OLD.year_code, OLD.name_ar, OLD.start_date, OLD.end_date, OLD.notes) THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة ومينفعش تتعدّل — افتحها الأول.', OLD.year_code;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER fiscal_years_closed_guard_trg
    BEFORE UPDATE OR DELETE ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_closed_guard();

-- مفيش فجوة: كل سنة بتبدأ تاني يوم بعد اللي قبلها على طول.
-- (مسح سنة في النص أو تقصيرها من ناحية جارتها = فجوة ← مرفوض.)
CREATE FUNCTION core.fiscal_years_no_gap() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    gap record;
BEGIN
    SELECT prev_code, year_code INTO gap
    FROM (SELECT year_code, start_date,
                 lag(end_date) OVER (ORDER BY start_date) AS prev_end,
                 lag(year_code) OVER (ORDER BY start_date) AS prev_code
          FROM core.fiscal_years) s
    WHERE prev_end IS NOT NULL AND start_date <> prev_end + 1
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'مينفعش يبقى فيه فجوة بين السنين المالية: السنة % لازم تبدأ تاني يوم بعد نهاية السنة %.',
            gap.year_code, gap.prev_code;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER fiscal_years_no_gap_trg
    AFTER INSERT OR UPDATE OR DELETE ON core.fiscal_years
    DEFERRABLE INITIALLY IMMEDIATE
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_no_gap();

-- ---------------------------------------------------------------------------
-- سجل التعديلات: «قفل / فتح» السنة إجراءين جداد
-- ---------------------------------------------------------------------------
ALTER TABLE sec.audit_log DROP CONSTRAINT audit_log_action_chk;
ALTER TABLE sec.audit_log ADD CONSTRAINT audit_log_action_chk CHECK (action IN (
    'create', 'update', 'delete', 'activate', 'deactivate', 'password',
    'permissions', 'post', 'unpost', 'approve', 'reverse', 'lock', 'unlock'));
