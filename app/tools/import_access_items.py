"""نقل الماركات والأصناف من ملف الأكسس لقاعدة البرنامج.

التشغيل (من فولدر المشروع):
    py -3.11 -m app.tools.import_access_items "Review1/ITEC6 (25.06.2026).accdb"

* بيتأكد إن سكريبتات القاعدة اتنفذت (زي فتح البرنامج) وبعدين بينقل في transaction واحد.
* لو اتشغّل تاني: الأصناف اللي اتنقلت قبل كده بتتعدّى (``access_id``).
"""

from __future__ import annotations

import sys

from app.database import migrator
from app.database.db import get_db
from app.services.access_import_service import AccessImportService, read_access


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    db = get_db()
    applied = migrator.migrate(db)
    if applied:
        print("سكريبتات القاعدة اللي اتنفذت:", ", ".join(getattr(m, "name", str(m)) for m in applied))
    data = read_access(argv[0])
    print(f"في الأكسس: {len(data['brands'])} ماركة · {len(data['shops'])} قسم · {len(data['items'])} صنف")
    report = AccessImportService(db).import_items(data)
    for line in report.lines():
        print("•", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
