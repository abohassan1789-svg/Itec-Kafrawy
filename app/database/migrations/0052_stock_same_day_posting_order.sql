-- 0052: ترتيب حركات المخزون في نفس اليوم **بترتيب الترحيل الفعلي** (رد المستخدم 2026-11-28).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- قبل كده (0043): جوه نفس اليوم «الوارد بتكلفة ← التحويلات ← الصادر» — فشراء اتعمل بعد ما الرصيد صفّر في نفس اليوم
-- كان بيدخل في متوسط البيع اللي اترحّل قبله (مثال المستخدم: 10 × 100 + 5 × 115 ← بيع 15 ← شراء 1 × 160 ← البيع خد
-- 108.44 بدل 105 والقطعة الباقية فضلت بـ 108.44 بدل 160).
-- دلوقتي (app/services/stock_ledger_service.py): الحركات بتتحسب بترتيب الترحيل (الـ id) وداخل التحويل ورا خارجه ←
-- **لما الرصيد يصفّر المتوسط بيبدأ من الأول بسعر الشراء الجديد** · البيع الأكبر من الرصيد اللي اترحّل قبل شراء نفس
-- اليوم (بصلاحية «من غير رصيد») بيطلع «ناقص» ويتصلّح بسعر الشراء ده.
--
-- السكريبت ده مابيقدرش يعيد الحساب لوحده (المحرك Python): بيسجّل **طلب إعادة حساب** لكل الأصناف اللي ليها حركات،
-- والبرنامج بينفّذه أول ما يفتح بعد السكريبتات (StockLedgerService.run_recompute_requests — مرة واحدة، بقفل صف الطلب
-- عشان جهازين مايعملوهوش مع بعض). القيم اللي اتقيّدت (booked_value) مابتتغيّرش — لو قيمة حركة اتغيّرت الفرق بيبان في
-- «فروق التكلفة» زي أي حركة بتاريخ قديم.

CREATE TABLE inv.stock_recompute_requests (
    id            integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason        varchar(200) NOT NULL,
    requested_at  timestamptz  NOT NULL DEFAULT now(),
    done_at       timestamptz,
    items_done    integer,
    CONSTRAINT stock_recompute_requests_reason_chk CHECK (btrim(reason) <> ''),
    CONSTRAINT stock_recompute_requests_done_chk CHECK ((done_at IS NULL) = (items_done IS NULL) AND
                                                         (items_done IS NULL OR items_done >= 0))
);
CREATE INDEX stock_recompute_requests_pending_idx ON inv.stock_recompute_requests (id) WHERE done_at IS NULL;
COMMENT ON TABLE inv.stock_recompute_requests IS
    'طلبات إعادة حساب دفتر المخزون لكل الأصناف (بعد تغيير قاعدة الحساب) — البرنامج بينفّذها وقت الفتح';

INSERT INTO inv.stock_recompute_requests (reason)
SELECT 'ترتيب نفس اليوم بقى بترتيب الترحيل الفعلي (0052)'
WHERE EXISTS (SELECT 1 FROM inv.stock_movements);
