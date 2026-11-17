-- 0039: الشيكات — «أوراق القبض» و«شيكات تحت التحصيل» و«أوراق الدفع» من غير بُعد العميل / المورد
--       (ملاحظة المستخدم 2026-11-17 على 0038).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- المشكلة: قيد الاستلام (مدين أوراق القبض / دائن العملاء) كان الطرفين فيه عليهم العميل، فكشف حساب العميل كان
-- بيبيّن الشيك **مرتين — مرة مدين ومرة دائن** ورصيده مابيتغيّرش. والصح (رد المستخدم): الشيك اللي استلمناه =
-- العميل دفع ← يبان في كشفه **دائن بس**، ولو رجع من البنك أو اترد ← مدين تاني.
-- الحل: حسابات الشيكات (1104 · 1108 · 2102) **مالهاش طرف** — والشيكات نفسها بعميلها / مورّدها في جدول الشيكات.
--   * دور الحساب والحساب نفسه ← party_kind = NULL · قواعد ترحيل الشيكات على الحسابات دي من غير طرف.
--   * سطور القيود اللي اتعملت قبل كده على الحسابات دي ← الطرف بيتشال (نفس المبالغ والحسابات — الأرصدة مابتتغيرش،
--     بس كشف العميل / المورد بيتظبط). التريجرات اللي بتمنع التغيير ده بتتقفل لحظة التصحيح بس وترجع على طول.

UPDATE acc.account_roles SET default_party_kind = NULL WHERE code IN ('notes_rec', 'notes_pay', 'notes_coll');

UPDATE acc.posting_rules SET party_kind = NULL
 WHERE (doc_type = 'cheque_in' AND role IN ('notes_dr', 'notes_cr', 'coll_dr', 'coll_cr'))
    OR (doc_type = 'cheque_out' AND role IN ('notes_dr', 'notes_cr'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM acc.opening_balance_lines l JOIN acc.accounts a ON a.id = l.account_id
                WHERE a.role_code IN ('notes_rec', 'notes_pay', 'notes_coll')
                  AND num_nonnulls(l.customer_id, l.supplier_id, l.employee_id, l.partner_id) > 0) THEN
        RAISE EXCEPTION 'فيه أرصدة افتتاحية على أوراق القبض / الدفع بالعميل أو المورد — لازم تتراجع يدوي قبل السكريبت ده.';
    END IF;
END $$;

ALTER TABLE acc.accounts DISABLE TRIGGER accounts_zjournal_guard_trg;
UPDATE acc.accounts SET party_kind = NULL
 WHERE role_code IN ('notes_rec', 'notes_pay', 'notes_coll') AND party_kind IS NOT NULL;
ALTER TABLE acc.accounts ENABLE TRIGGER accounts_zjournal_guard_trg;

ALTER TABLE acc.journal_lines DISABLE TRIGGER journal_lines_guard_trg;
UPDATE acc.journal_lines l SET customer_id = NULL, supplier_id = NULL
  FROM acc.accounts a
 WHERE a.id = l.account_id AND a.role_code IN ('notes_rec', 'notes_pay', 'notes_coll')
   AND (l.customer_id IS NOT NULL OR l.supplier_id IS NOT NULL);
ALTER TABLE acc.journal_lines ENABLE TRIGGER journal_lines_guard_trg;
