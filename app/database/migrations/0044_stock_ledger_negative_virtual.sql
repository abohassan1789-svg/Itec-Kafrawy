-- 0044: ردود المستخدم على «دفتر المخزون» (2026-11-23).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
--   * **الرصيد السالب مسموح بصلاحية** (صلاحية «من غير رصيد» في كل شاشة لوحدها) ← اتشال منع السالب من جداول الأرصدة.
--     المنع نفسه في المحرك (app/services/stock_ledger_service.py): من غير الصلاحية أي يوم يبقى بالسالب أكتر من الأول ← رفض.
--   * **تكلفة اللي اتصرف بالسالب بتتصلّح بسعر الشراء اللي غطّاه** (المحرك) — والفرق بيبان في «فروق التكلفة» (value ≠ booked_value).
--   * **التحويل بين المخزن الوهمي والحقيقي مسموح في الاتجاهين** (الوهمي فاضل «متابعة كميات بس» من غير أثر محاسبي):
--       حقيقي ← وهمي: الخارج من الحقيقي بالمتوسط (بيطلع من قيمة المخزون — قيده على مصروف في مستند التحويل) والداخل كمية بس.
--       وهمي ← حقيقي: الداخل للحقيقي **بآخر متوسط** (cost_mode = current) — وقيده عكس اللي فوق.
--   * التاريخ القديم مسموح زي ما هو · «تحت حد الطلب» = الرصيد ≤ الحد (زي ما هو).

-- ---------------------------------------------------------------------------
-- 1) الأرصدة ممكن تبقى بالسالب (بالصلاحية)
-- ---------------------------------------------------------------------------
ALTER TABLE inv.stock_balances DROP CONSTRAINT stock_balances_qty_chk;
ALTER TABLE inv.item_costs DROP CONSTRAINT item_costs_qty_chk;
ALTER TABLE inv.item_costs ADD CONSTRAINT item_costs_avg_chk CHECK (avg_cost >= 0);

-- ---------------------------------------------------------------------------
-- 2) طريقة جديدة للداخل: current = بآخر متوسط (وهمي ← حقيقي)
-- ---------------------------------------------------------------------------
ALTER TABLE inv.stock_movements DROP CONSTRAINT stock_movements_mode_chk;
ALTER TABLE inv.stock_movements DROP CONSTRAINT stock_movements_mode_rules;
ALTER TABLE inv.stock_movements
    ADD CONSTRAINT stock_movements_mode_chk CHECK (cost_mode IN ('given', 'average', 'transfer', 'current', 'none')),
    ADD CONSTRAINT stock_movements_mode_rules CHECK (
        (cost_mode = 'none') = is_virtual
        AND (cost_mode = 'given') = (given_cost IS NOT NULL)
        AND (given_cost IS NULL OR given_cost >= 0)
        AND (cost_mode NOT IN ('given', 'transfer', 'current') OR qty > 0)
        AND (cost_mode <> 'average' OR qty < 0)
        AND (cost_mode NOT IN ('transfer', 'current') OR pair_id IS NOT NULL)
        AND (pair_id IS NULL OR qty > 0));
COMMENT ON COLUMN inv.stock_movements.cost_mode IS
    'given = داخل بتكلفة من المستند · average = خارج بالمتوسط · transfer = داخل بقيمة الخارج اللي قصاده · '
    'current = داخل بآخر متوسط (من مخزن وهمي) · none = مخزن وهمي (كمية بس)';

-- ---------------------------------------------------------------------------
-- 3) الحماية: التحويل بين وهمي وحقيقي مسموح (بطريقة التكلفة الصح)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION inv.stock_movements_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    y   core.fiscal_years%ROWTYPE;
    d   date;
    it  inv.items%ROWTYPE;
    wh  inv.warehouses%ROWTYPE;
    pr  inv.stock_movements%ROWTYPE;
    want text;
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
               OR pr.source_id <> NEW.source_id OR pr.movement_date <> NEW.movement_date THEN
                RAISE EXCEPTION 'الداخل في التحويل لازم يبقى نفس الصنف والكمية والتاريخ والمستند بتاع الخارج.';
            END IF;
            IF (pr.warehouse_id, pr.stock_kind) = (NEW.warehouse_id, NEW.stock_kind) THEN
                RAISE EXCEPTION 'التحويل لنفس المخزن ونفس النوع (فاتورة / بيان) مالوش معنى.';
            END IF;
            -- وهمي ← حقيقي: الداخل بآخر متوسط (current) · حقيقي ← وهمي: الداخل كمية بس (none) · حقيقي ← حقيقي: transfer.
            want := CASE WHEN NEW.is_virtual THEN 'none' WHEN pr.is_virtual THEN 'current' ELSE 'transfer' END;
            IF NEW.cost_mode <> want THEN
                RAISE EXCEPTION 'طريقة تكلفة الداخل في التحويل مش مظبوطة.';
            END IF;
        END IF;
        NEW.created_at := now();
    END IF;
    RETURN NEW;
END $$;
