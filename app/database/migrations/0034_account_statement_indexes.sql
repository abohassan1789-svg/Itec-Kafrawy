-- 0034: indexes «كشف الحساب ودفتر الأستاذ» (المرحلة 4، النموذج 1 «فلاتر فوق + جدول» — اختيار المستخدم 2026-11-17).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- الكشف قراية بس من سطور القيود المرحّلة (acc.journal_lines + acc.journal_entries) — مفيش جداول جديدة.
-- الرصيد السابق بـ SUM واحد في القاعدة، والحركات بصفحات؛ الـ indexes دي بتخلّي الاتنين يتقروا من الـ index نفسه
-- (index-only) لما الحساب أو الطرف يبقى عليه حركات كتير:
--   * السطور بالحساب / بالطرف + القيد، ومعاهم المدين والدائن (INCLUDE) ← الـ SUM مابيروحش للجدول.
--   * القيود المرحّلة بس (partial) بالـ id ومعاها التاريخ ← فلتر «المرحّل بس» والفترة من الـ index.
-- الـ indexes القديمة بتاعة 0031 على نفس الأعمدة بتتشال (الجديدة بتغطي نفس البحث + الـ FK).

DROP INDEX acc.journal_lines_account_idx;
CREATE INDEX journal_lines_account_idx ON acc.journal_lines (account_id, entry_id) INCLUDE (debit, credit);

DROP INDEX acc.journal_lines_customer_idx;
CREATE INDEX journal_lines_customer_idx ON acc.journal_lines (customer_id, entry_id) INCLUDE (debit, credit)
    WHERE customer_id IS NOT NULL;

DROP INDEX acc.journal_lines_supplier_idx;
CREATE INDEX journal_lines_supplier_idx ON acc.journal_lines (supplier_id, entry_id) INCLUDE (debit, credit)
    WHERE supplier_id IS NOT NULL;

DROP INDEX acc.journal_lines_employee_idx;
CREATE INDEX journal_lines_employee_idx ON acc.journal_lines (employee_id, entry_id) INCLUDE (debit, credit)
    WHERE employee_id IS NOT NULL;

DROP INDEX acc.journal_lines_partner_idx;
CREATE INDEX journal_lines_partner_idx ON acc.journal_lines (partner_id, entry_id) INCLUDE (debit, credit)
    WHERE partner_id IS NOT NULL;

CREATE INDEX journal_entries_posted_idx ON acc.journal_entries (id) INCLUDE (entry_date)
    WHERE status = 'posted';
COMMENT ON INDEX acc.journal_entries_posted_idx IS 'كشف الحساب: القيود المرحّلة بس بتاريخها (index-only مع سطور الحساب/الطرف)';
