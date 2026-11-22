-- 0043: «دفتر المخزون» (المرحلة 6 — من غير شاشة · المحرك اللي كل فواتير البيع والشراء والتحويلات والجرد هتستعمله).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- القرارات (ملف 03 القسم 9.5 + 9.4):
--   * **فاتورة / بيان = مخزون منفصل**: الرصيد والمتوسط لكل (صنف + نوع المخزون) — `stock_kind` = invoice / statement.
--   * **متوسط مرجّح مستمر على مستوى الشركة** لكل (صنف + نوع): التحويل بين المخازن مابيغيّرش التكلفة.
--     الكمية بتتابع كمان لكل (صنف + مخزن + نوع).
--   * **الرصيد السالب ممنوع** — في أي يوم، مش النهارده بس (حركة بتاريخ قديم مايتسمحش لو هتخلّي يوم بعده بالسالب).
--   * **المخزن الوهمي** = كميات بس من غير تكلفة ولا قيمة، ومابيدخلش في المتوسط ولا في قيمة المخزون.
--   * ترتيب نفس اليوم: **الوارد بتكلفة الأول ← التحويلات (الخارج وبعده الداخل بنفس التكلفة) ← الصادر بالمتوسط**،
--     وجوه كل مجموعة بالترتيب اللي اتسجلت بيه. الرصيد السالب بيتشاف **آخر اليوم** (البيع والشراء في نفس اليوم عادي).
--   * **التكلفة بتتجمّد** على الحركة (`unit_cost` / `value`) — والقيمة اللي اتقيّدت فعلاً في القيد `booked_value`.
--     لو حركة بتاريخ قديم اتسجلت أو اتلغت، المحرك **بيعيد حساب** المتوسط والتكلفة للحركات اللي بعدها،
--     والفرق (`value − booked_value`) هو «فروق التكلفة» اللي بندها هيقيّدها بعدين.
--   * الجداول دي **المحرك بس اللي بيكتب فيها** (app/services/stock_ledger_service.py) جوه transaction المستند،
--     وبعد قفل صف الصنف (`FOR NO KEY UPDATE`) — جهازين بيبيعوا نفس الصنف في نفس اللحظة بيتصفّوا ورا بعض.

-- ---------------------------------------------------------------------------
-- 1) أنواع المستندات اللي بتعمل حركة مخزون
-- ---------------------------------------------------------------------------
CREATE TABLE inv.stock_doc_types (
    code        varchar(30)  PRIMARY KEY,
    name_ar     varchar(60)  NOT NULL,
    sort_order  smallint     NOT NULL,
    CONSTRAINT stock_doc_types_code_chk CHECK (code ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT stock_doc_types_name_chk CHECK (btrim(name_ar) <> '')
);
COMMENT ON TABLE inv.stock_doc_types IS 'أنواع المستندات اللي بتعمل حركة في دفتر المخزون';

INSERT INTO inv.stock_doc_types (code, name_ar, sort_order) VALUES
    ('opening_stock',    'بضاعة أول المدة',     1),
    ('purchase_invoice', 'فاتورة شراء',         2),
    ('purchase_return',  'مردود شراء',          3),
    ('sales_invoice',    'إذن صرف (فاتورة بيع)', 4),
    ('sales_return',     'مردود بيع',           5),
    ('transfer',         'تحويل بين المخازن',   6),
    ('stock_adjustment', 'تسوية جرد',           7);

-- ---------------------------------------------------------------------------
-- 2) دفتر المخزون: كل دخول وخروج بتكلفته + الرصيد والمتوسط بعده
-- ---------------------------------------------------------------------------
CREATE TABLE inv.stock_movements (
    id                bigint         GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type       varchar(30)    NOT NULL REFERENCES inv.stock_doc_types (code) ON DELETE RESTRICT,
    source_id         integer        NOT NULL,
    source_no         varchar(40)    NOT NULL,
    line_no           smallint       NOT NULL,
    movement_date     date           NOT NULL,
    item_id           integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    warehouse_id      integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    stock_kind        varchar(10)    NOT NULL,
    qty               numeric(18,6)  NOT NULL,                 -- بالوحدة الأساسية: + داخل · − خارج
    -- given = داخل بتكلفة من المستند · average = خارج بالمتوسط · transfer = داخل بتكلفة الخارج اللي قصاده
    -- none = مخزن وهمي (كمية بس)
    cost_mode         varchar(10)    NOT NULL,
    given_cost        numeric(18,6),                           -- تكلفة الوحدة من المستند (given بس)
    pair_id           bigint         REFERENCES inv.stock_movements (id),   -- الداخل ← الخارج بتاعه (بيتمسحوا مع بعض)
    is_virtual        boolean        NOT NULL DEFAULT false,   -- من المخزن وقت الحركة (والمخزن مايتغيّرش بعدها)
    -- بيحسبها المحرك (وبيعيد حسابها لو حركة بتاريخ قديم اتسجلت أو اتلغت):
    unit_cost         numeric(18,6),                           -- NULL للوهمي
    value             numeric(18,2)  NOT NULL DEFAULT 0,       -- بالإشارة: + داخل · − خارج (صفر للوهمي)
    booked_value      numeric(18,2)  NOT NULL DEFAULT 0,       -- القيمة اللي اتقيّدت (الفرق = فروق التكلفة)
    kind_qty_after    numeric(18,6),                           -- رصيد (الصنف + النوع) في الشركة بعد الحركة
    kind_value_after  numeric(18,2),
    avg_cost_after    numeric(18,6),                           -- المتوسط بعد الحركة
    wh_qty_after      numeric(18,6)  NOT NULL DEFAULT 0,       -- رصيد (الصنف + المخزن + النوع) بعد الحركة
    day_seq           integer        NOT NULL DEFAULT 0,       -- ترتيبها جوه اليوم (بيحطه المحرك)
    created_at        timestamptz    NOT NULL DEFAULT now(),
    created_by        integer        REFERENCES sec.app_users (id) ON DELETE SET NULL,
    CONSTRAINT stock_movements_line_unique UNIQUE (source_type, source_id, line_no),
    CONSTRAINT stock_movements_line_chk CHECK (line_no > 0),
    CONSTRAINT stock_movements_no_chk CHECK (btrim(source_no) <> ''),
    CONSTRAINT stock_movements_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT stock_movements_qty_chk CHECK (qty <> 0),
    CONSTRAINT stock_movements_mode_chk CHECK (cost_mode IN ('given', 'average', 'transfer', 'none')),
    CONSTRAINT stock_movements_mode_rules CHECK (
        (cost_mode = 'none') = is_virtual
        AND (cost_mode = 'given') = (given_cost IS NOT NULL)
        AND (given_cost IS NULL OR given_cost >= 0)
        AND (cost_mode NOT IN ('given', 'transfer') OR qty > 0)
        AND (cost_mode <> 'average' OR qty < 0)
        AND (cost_mode <> 'transfer' OR pair_id IS NOT NULL)
        AND (pair_id IS NULL OR qty > 0)),
    CONSTRAINT stock_movements_cost_chk CHECK (
        (is_virtual AND unit_cost IS NULL AND value = 0 AND booked_value = 0 AND kind_qty_after IS NULL
         AND kind_value_after IS NULL AND avg_cost_after IS NULL)
        OR (NOT is_virtual AND (unit_cost IS NULL OR unit_cost >= 0))),
    CONSTRAINT stock_movements_pair_self CHECK (pair_id IS NULL OR pair_id <> id)
);
CREATE INDEX stock_movements_chain_idx ON inv.stock_movements (item_id, stock_kind, movement_date, day_seq);
CREATE INDEX stock_movements_wh_idx ON inv.stock_movements (item_id, warehouse_id, stock_kind, movement_date);
CREATE INDEX stock_movements_warehouse_idx ON inv.stock_movements (warehouse_id, movement_date);
CREATE INDEX stock_movements_source_idx ON inv.stock_movements (source_type, source_id);
CREATE INDEX stock_movements_date_idx ON inv.stock_movements (movement_date);
CREATE INDEX stock_movements_pair_idx ON inv.stock_movements (pair_id) WHERE pair_id IS NOT NULL;
CREATE INDEX stock_movements_user_idx ON inv.stock_movements (created_by) WHERE created_by IS NOT NULL;
CREATE INDEX stock_movements_cost_diff_idx ON inv.stock_movements (source_type, source_id)
    WHERE value <> booked_value;
COMMENT ON TABLE inv.stock_movements IS
    'دفتر المخزون: كل حركة (صنف · مخزن · فاتورة/بيان · كمية ±) بتكلفتها المتجمّدة والرصيد والمتوسط بعدها — المحرك بس بيكتب فيه';

-- ---------------------------------------------------------------------------
-- 3) الأرصدة الحالية (بيحدّثها المحرك جوه قفل الصنف) — مايبقاش فيها سالب أبداً
-- ---------------------------------------------------------------------------
CREATE TABLE inv.stock_balances (
    item_id       integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    warehouse_id  integer        NOT NULL REFERENCES inv.warehouses (id) ON DELETE RESTRICT,
    stock_kind    varchar(10)    NOT NULL,
    qty           numeric(18,6)  NOT NULL,
    updated_at    timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, warehouse_id, stock_kind),
    CONSTRAINT stock_balances_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT stock_balances_qty_chk CHECK (qty >= 0)
);
CREATE INDEX stock_balances_warehouse_idx ON inv.stock_balances (warehouse_id);
COMMENT ON TABLE inv.stock_balances IS 'رصيد كل (صنف + مخزن + فاتورة/بيان) دلوقتي — من دفتر المخزون';

CREATE TABLE inv.item_costs (
    item_id       integer        NOT NULL REFERENCES inv.items (id) ON DELETE RESTRICT,
    stock_kind    varchar(10)    NOT NULL,
    qty           numeric(18,6)  NOT NULL,
    value         numeric(18,2)  NOT NULL,
    avg_cost      numeric(18,6)  NOT NULL,
    last_date     date,
    updated_at    timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, stock_kind),
    CONSTRAINT item_costs_kind_chk CHECK (stock_kind IN ('invoice', 'statement')),
    CONSTRAINT item_costs_qty_chk CHECK (qty >= 0 AND avg_cost >= 0)
);
COMMENT ON TABLE inv.item_costs IS
    'رصيد وقيمة ومتوسط تكلفة كل (صنف + فاتورة/بيان) على مستوى الشركة دلوقتي (من غير المخازن الوهمية)';

-- ---------------------------------------------------------------------------
-- 4) حماية الحركة نفسها: السنة مفتوحة · الصنف مش خدمي · الوهمي من المخزن · البيانات الأصلية ثابتة
-- ---------------------------------------------------------------------------
CREATE FUNCTION inv.stock_movements_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y   core.fiscal_years%ROWTYPE;
    d   date;
    it  inv.items%ROWTYPE;
    wh  inv.warehouses%ROWTYPE;
    pr  inv.stock_movements%ROWTYPE;
BEGIN
    d := CASE WHEN TG_OP = 'DELETE' THEN OLD.movement_date ELSE NEW.movement_date END;
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.source_type, NEW.source_id, NEW.source_no, NEW.line_no, NEW.movement_date, NEW.item_id,
            NEW.warehouse_id, NEW.stock_kind, NEW.qty, NEW.cost_mode, NEW.given_cost, NEW.pair_id, NEW.is_virtual,
            NEW.created_at, NEW.created_by)
           IS DISTINCT FROM
           (OLD.source_type, OLD.source_id, OLD.source_no, OLD.line_no, OLD.movement_date, OLD.item_id,
            OLD.warehouse_id, OLD.stock_kind, OLD.qty, OLD.cost_mode, OLD.given_cost, OLD.pair_id, OLD.is_virtual,
            OLD.created_at, OLD.created_by) THEN
            RAISE EXCEPTION 'حركة المخزون مابتتعدّلش — المستند بيمسح حركاته ويسجّلها تاني.';
        END IF;
        IF (NEW.unit_cost, NEW.value, NEW.booked_value)
           IS NOT DISTINCT FROM (OLD.unit_cost, OLD.value, OLD.booked_value) THEN
            RETURN NEW;                  -- الرصيد بعد الحركة بس اتغيّر (من غير تكلفة) — مسموح
        END IF;
    END IF;

    SELECT * INTO y FROM core.fiscal_years WHERE d BETWEEN start_date AND end_date FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'مفيش سنة مالية فيها تاريخ % — ضيفها الأول من شاشة «السنة المالية والفترات».',
            to_char(d, 'DD/MM/YYYY');
    END IF;
    IF y.status = 'closed' THEN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'السنة المالية % مقفولة — الحركة دي هتغيّر تكلفة حركات مخزون فيها (يوم %).',
                y.year_code, to_char(d, 'DD/MM/YYYY');
        END IF;
        RAISE EXCEPTION 'السنة المالية % مقفولة — مينفعش تتسجل أو تتلغى فيها حركة مخزون.', y.year_code;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO it FROM inv.items WHERE id = NEW.item_id;
        IF it.is_service THEN
            RAISE EXCEPTION 'الصنف % «%» خدمي — مالوش حركة مخزون.', 'ITM-' || lpad(it.seq::text, 4, '0'), it.name;
        END IF;
        SELECT * INTO wh FROM inv.warehouses WHERE id = NEW.warehouse_id;
        IF NEW.is_virtual <> wh.is_virtual THEN
            RAISE EXCEPTION 'المخزن «%» % — الحركة لازم تتسجل كده.', wh.name_ar,
                CASE WHEN wh.is_virtual THEN 'وهمي' ELSE 'مش وهمي' END;
        END IF;
        IF NEW.pair_id IS NOT NULL THEN
            SELECT * INTO pr FROM inv.stock_movements WHERE id = NEW.pair_id;
            IF pr.item_id <> NEW.item_id OR pr.qty <> -NEW.qty OR pr.source_type <> NEW.source_type
               OR pr.source_id <> NEW.source_id OR pr.is_virtual <> NEW.is_virtual
               OR pr.movement_date <> NEW.movement_date THEN
                RAISE EXCEPTION 'الداخل في التحويل لازم يبقى نفس الصنف والكمية والتاريخ والمستند بتاع الخارج.';
            END IF;
            IF (pr.warehouse_id, pr.stock_kind) = (NEW.warehouse_id, NEW.stock_kind) THEN
                RAISE EXCEPTION 'التحويل لنفس المخزن ونفس النوع (فاتورة / بيان) مالوش معنى.';
            END IF;
        END IF;
        NEW.created_at := now();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stock_movements_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON inv.stock_movements
    FOR EACH ROW EXECUTE FUNCTION inv.stock_movements_guard();

-- ---------------------------------------------------------------------------
-- 5) الصنف والمخزن والسنة اللي عليهم حركة
-- ---------------------------------------------------------------------------
-- الصنف: مايتمسحش · الوحدة الأساسية و«خدمي» مايتغيّروش بعد أول حركة.
CREATE FUNCTION inv.items_stock_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
    label text;
BEGIN
    label := 'ITM-' || lpad(OLD.seq::text, 4, '0') || ' «' || OLD.name || '»';
    IF TG_OP = 'UPDATE' AND NEW.unit_id = OLD.unit_id AND NEW.is_service = OLD.is_service THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO n FROM inv.stock_movements WHERE item_id = OLD.id;
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'الصنف % عليه % حركة مخزون — مايتمسحش (وقّفه بدل ما تمسحه).', label, n;
    END IF;
    IF NEW.unit_id <> OLD.unit_id THEN
        RAISE EXCEPTION 'الصنف % عليه حركة مخزون — الوحدة الأساسية مابتتغيّرش (الأرصدة متسجلة بيها).', label;
    END IF;
    RAISE EXCEPTION 'الصنف % عليه حركة مخزون — مايتحوّلش %.', label,
        CASE WHEN NEW.is_service THEN 'خدمي' ELSE 'من خدمي لمخزني' END;
END $$;
CREATE TRIGGER items_stock_guard_trg
    BEFORE UPDATE OF unit_id, is_service OR DELETE ON inv.items
    FOR EACH ROW EXECUTE FUNCTION inv.items_stock_guard();

-- المخزن: مايتمسحش · «وهمي» مايتغيّرش بعد أول حركة.
CREATE FUNCTION inv.warehouses_stock_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.is_virtual = OLD.is_virtual THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO n FROM inv.stock_movements WHERE warehouse_id = OLD.id;
    IF n = 0 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'المخزن «%» عليه % حركة مخزون — مايتمسحش (وقّفه بدل ما تمسحه).', OLD.name_ar, n;
    END IF;
    RAISE EXCEPTION 'المخزن «%» عليه حركة مخزون — «وهمي» مايتغيّرش بعد كده.', OLD.name_ar;
END $$;
CREATE TRIGGER warehouses_stock_guard_trg
    BEFORE UPDATE OF is_virtual OR DELETE ON inv.warehouses
    FOR EACH ROW EXECUTE FUNCTION inv.warehouses_stock_guard();

-- السنة المالية: اللي فيها حركة مخزون ماتتمسحش · تواريخها ماتسيبش حركة برّاها.
CREATE FUNCTION core.fiscal_years_stock_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT count(*) INTO n FROM inv.stock_movements
         WHERE movement_date BETWEEN OLD.start_date AND OLD.end_date;
        IF n > 0 THEN
            RAISE EXCEPTION 'السنة المالية % فيها % حركة مخزون — ماتتمسحش.', OLD.year_code, n;
        END IF;
        RETURN OLD;
    END IF;
    IF (NEW.start_date, NEW.end_date) IS DISTINCT FROM (OLD.start_date, OLD.end_date) THEN
        SELECT count(*) INTO n FROM inv.stock_movements
         WHERE movement_date BETWEEN OLD.start_date AND OLD.end_date
           AND movement_date NOT BETWEEN NEW.start_date AND NEW.end_date;
        IF n > 0 THEN
            RAISE EXCEPTION 'فيه % حركة مخزون في السنة % تاريخها برّا المدة الجديدة — مينفعش تغيّر التواريخ كده.',
                n, OLD.year_code;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fiscal_years_zstock_guard_trg
    BEFORE UPDATE OR DELETE ON core.fiscal_years
    FOR EACH ROW EXECUTE FUNCTION core.fiscal_years_stock_guard();
