"""شجرة القائمة (الشريط الأخضر + القائمة المنبثقة) حسب صلاحيات المستخدم — من غير Qt.

* الشاشة اللي مالكش عليها «عرض» **مابتظهرش**.
* قسم كل شاشاته ممنوعة عليك **مابيظهرش**.
* قسم لسه مالوش شاشات خالص (هيتملى في المراحل الجاية) بيظهر بعلامة «قريباً».
"""

from __future__ import annotations

from typing import Any, Callable

from app.services import permission_registry as reg

CanFn = Callable[[str], bool]


def view_code(module_code: str, target_code: str) -> str:
    return reg.permission_code(module_code, target_code, "view")


def menu_tree(can: CanFn) -> list[dict[str, Any]]:
    tree: list[dict[str, Any]] = []
    for s in reg.SECTIONS:
        has_targets = bool(s.targets())
        groups = []
        for g in s.groups:
            items = [{"key": t.code, "module": s.id, "title": t.name_ar, "icon": t.icon or s.icon,
                      "kind": t.kind}
                     for t in g.targets if can(view_code(s.id, t.code))]
            if items:
                groups.append({"title": g.title, "icon": g.icon, "items": items})
        if has_targets and not groups:
            continue
        tree.append({"id": s.id, "title": s.title, "icon": s.icon, "bottom": s.bottom,
                     "coming_soon": not has_targets, "groups": groups,
                     "count": sum(len(g["items"]) for g in groups)})
    return tree


def find_item(tree: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for sec in tree:
        for group in sec["groups"]:
            for item in group["items"]:
                if item["key"] == key:
                    return item
    return None
