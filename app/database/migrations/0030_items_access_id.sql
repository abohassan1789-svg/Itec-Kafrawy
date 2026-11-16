-- 0030: رقم الصنف في الأكسس (طلب المستخدم 2026-11-16: «جيب الأصناف اللي في الأكسس» — النقل دلوقتي مش في المرحلة 11).
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
--   * `access_id` = `pruduct.id` في الأكسس: النقل مايتكررش لو اتشغّل تاني (الصنف اللي اتنقل بيتعدّى)،
--     وفواتير الأكسس (Sallesdata / purchaseData / montagatData … عمود item_id) هتتربط بيه وقت نقلها.
--   * الأصناف الجديدة من البرنامج `access_id` بتاعها فاضي.
--   * بيانات الأكسس نفسها مش في السكريبت ده — بتتقري وقت التشغيل (`app/tools/import_access_items.py`).

ALTER TABLE inv.items ADD COLUMN access_id integer;
ALTER TABLE inv.items ADD CONSTRAINT items_access_id_chk CHECK (access_id IS NULL OR access_id > 0);
CREATE UNIQUE INDEX items_access_id_unique ON inv.items (access_id) WHERE access_id IS NOT NULL;
COMMENT ON COLUMN inv.items.access_id IS 'رقم الصنف في الأكسس (pruduct.id) — للنقل وربط الفواتير القديمة';
