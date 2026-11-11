-- 0018: صيغة رقم أمر التوريد النهائية (رد المستخدم 2026-11-11): Itec-001 · Itec-002 · …
-- ممنوع تعديل الملف ده بعد ما يتنفذ؛ أي تغيير يبقى في سكريبت جديد.
--
-- {SEQ} بأقل حاجة 3 خانات بأصفار على الشمال، ولما يعدّي 999 بيكمّل من غير أصفار ومن غير أي خطأ
-- (acc.format_project_no من 0016): Itec-999 ← Itec-1000 ← … ← Itec-1000000 (العمود code varchar(30)
-- والعدّاد integer — يعني لحد أكتر من 2 مليار أمر).
-- العدّاد نفسه (last_no) زي ما هو — الأرقام اللي اتصرفت قبل كده مابتتكررش، والأرقام المكتوبة بالإيد بتتعدّى.

UPDATE acc.project_numbering
   SET pattern = 'Itec-{SEQ}',
       digits  = 3
 WHERE id = 1;

COMMENT ON TABLE acc.project_numbering IS
    'شكل رقم أمر التوريد (Itec-{SEQ} = Itec-001… وبعد 999 بيكمّل Itec-1000) وآخر مسلسل اتصرف — سجل واحد';
COMMENT ON FUNCTION acc.next_project_no(date) IS
    'بيصرف رقم أمر التوريد الجاي (Itec-001…) جوه قفل — بيتنادى في نفس transaction الحفظ';
