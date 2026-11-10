"""مصفوفة تخصيص صلاحيات المستخدم — النموذج 1 «زي Hr GAD: مصفوفة الصلاحية الفعلية بالألوان» (اختيار المستخدم 2026-11-07).

صف لكل شاشة (مترتبة زي القائمة الجانبية بعناوين الأقسام) وعمود لكل إجراء. كل خانة = **الصلاحية الفعلية**:
    ✔ أخضر = جاية من المجموعة · ✔ أزرق = سماح خاص · ✖ أحمر = منع خاص · ☐ رمادي = ممنوع · 🔑 = بكلمة سر.
الضغط على الخانة (في «تعديل») بيلف: وراثة ← سماح ← منع ← وراثة. كليك يمين = الخانة / الشاشة كلها / القسم كله.
«حذف الكل» و«إظهار الإجماليات» لما يبقوا «سماح» ومفيش كلمة سر ← بتتسأل عن كلمة سر جديدة (الإلغاء = مفيش تغيير).

المحرر واجهة بس: بيشيل التخصيصات وكلمات السر الجديدة لحد ما الشاشة تحفظ.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import permission_registry as reg
from app.ui.common.theme import FIRST_COLUMN, GREEN, RED, TEXT_MUTED

INHERIT, ALLOW, DENY = "inherit", "allow", "deny"
CYCLE = {INHERIT: ALLOW, ALLOW: DENY, DENY: INHERIT}
STATE_AR = {INHERIT: "وراثة", ALLOW: "سماح", DENY: "منع"}
BLUE = "#2563EB"
GREY = "#94A3B8"
HEADER_BG = {0: "#DCFCE7", 1: "#F0FDF4"}
OVERRIDE_BG = {ALLOW: "#EFF6FF", DENY: "#FEF2F2"}
PID_ROLE = Qt.UserRole + 1
PasswordAsker = Callable[[str, str], "str | None"]   # (فين، الإجراء) ← كلمة سر أو None

_ORDER: list[str] = []
for _code, _ar, _en in (*reg.SCREEN_ACTIONS, *reg.REPORT_ACTIONS):
    if _code not in _ORDER:
        _ORDER.append(_code)

FILTERS = (("all", "الكل"), ("overrides", "التخصيصات بس"), ("denied", "الممنوع بس"), ("allowed", "المسموح بس"))


class OverrideMatrixEditor(QWidget):
    def __init__(self, ask_password: PasswordAsker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ask_password = ask_password
        self.entries: list[dict[str, Any]] = []
        self.targets: dict[str, dict[str, Any]] = {}
        self.actions: list[str] = []
        self.info: dict[int, dict[str, Any]] = {}       # من ``PermissionService.user_matrix``
        self.states: dict[int, str] = {}                # التخصيص الحالي (بعد التعديل)
        self.pending_passwords: dict[int, str] = {}
        self.is_full = False
        self.editable = False
        self._cells: dict[int, tuple[int, int]] = {}    # pid ← (صف، عمود)
        self._row_kind: list[tuple[str, Any]] = []      # لكل صف: ("header", entry) أو ("target", key)
        self._build_ui()

    # ---- البناء ------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        top = QHBoxLayout()
        self.top_bar = top
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 بحث عن شاشة…")
        self.search.textChanged.connect(self.apply_filter)
        top.addWidget(self.search, 1)
        self.filter_combo = QComboBox()
        for code, text in FILTERS:
            self.filter_combo.addItem(text, code)
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        top.addWidget(self.filter_combo)
        root.addLayout(top)

        self.legend = QLabel(
            f"<span style='color:{GREEN}'>✔ من المجموعة</span> &nbsp;·&nbsp; "
            f"<span style='color:{BLUE}'>✔ سماح خاص</span> &nbsp;·&nbsp; "
            f"<span style='color:{RED}'>✖ منع خاص</span> &nbsp;·&nbsp; "
            f"<span style='color:{GREY}'>☐ ممنوع</span> &nbsp;·&nbsp; 🔑 بكلمة سر"
            f" &nbsp;&nbsp;<span style='color:{TEXT_MUTED}'>(اضغط على الخانة: وراثة ← سماح ← منع · كليك يمين للشاشة أو القسم كله)</span>")
        self.legend.setStyleSheet("font-weight:800;")
        root.addWidget(self.legend)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setStyleSheet("QTableWidget { font-size:15px; } "
                                 "QTableWidget::item:selected { background:#DDF3E6; color:#111827; }")
        root.addWidget(self.table, 1)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        root.addWidget(self.summary)

    # ---- تحميل -------------------------------------------------------------
    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """صفوف ``PermissionService.grouped_matrix()``."""
        self.entries = entries
        self.targets = {e["row"]["key"]: e["row"] for e in entries if e["kind"] == "target"}
        used = {a for row in self.targets.values() for a in row["actions"]}
        self.actions = [a for a in _ORDER if a in used]
        table = self.table
        table.setUpdatesEnabled(False)
        table.clear()
        table.clearSpans()
        table.setColumnCount(1 + len(self.actions))
        # الاسم اللي فيه كلمتين على سطرين («إظهار الإجماليات») عشان العمود مايتقصّش.
        table.setHorizontalHeaderLabels(["الشاشة"] + [reg.ACTION_NAMES_AR[a].replace(" ", "\n")
                                                      for a in self.actions])
        table.setRowCount(len(entries))
        self._cells = {}
        self._row_kind = []
        bold = QFont()
        bold.setBold(True)
        for r, entry in enumerate(entries):
            if entry["kind"] == "header":
                indent = "      " if entry["level"] else ""
                item = QTableWidgetItem(f"{indent}{entry['title']}  ({entry['count']})")
                item.setFont(bold)
                item.setForeground(QBrush(QColor(GREEN)))
                item.setBackground(QBrush(QColor(HEADER_BG[entry["level"]])))
                table.setItem(r, 0, item)
                table.setSpan(r, 0, 1, table.columnCount())
                self._row_kind.append(("header", entry))
                continue
            row = entry["row"]
            name = QTableWidgetItem(row["target_name_ar"])
            name.setBackground(QBrush(QColor(FIRST_COLUMN)))
            table.setItem(r, 0, name)
            for c, action in enumerate(self.actions, start=1):
                item = QTableWidgetItem("")
                item.setTextAlignment(Qt.AlignCenter)
                pid = row["actions"].get(action)
                if pid is not None:
                    item.setData(PID_ROLE, int(pid))
                    self._cells[int(pid)] = (r, c)
                table.setItem(r, c, item)
            self._row_kind.append(("target", row["key"]))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for c in range(1, table.columnCount()):
            table.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch)
        table.setUpdatesEnabled(True)
        self.load({}, False)

    def load(self, info: dict[int, dict[str, Any]], is_full: bool) -> None:
        self.info = {pid: info.get(pid, {"role": False, "override": INHERIT, "has_password": False,
                                         "role_has_password": False}) for pid in self._cells}
        self.states = {pid: self.info[pid].get("override", INHERIT) for pid in self._cells}
        self.pending_passwords = {}
        self.is_full = bool(is_full)
        self.refresh()

    def set_editable(self, editable: bool) -> None:
        self.editable = bool(editable) and not self.is_full

    # ---- الحالة ------------------------------------------------------------
    def action_of(self, pid: int) -> str:
        for row in self.targets.values():
            for action, action_pid in row["actions"].items():
                if int(action_pid) == pid:
                    return action
        return ""

    def target_of(self, pid: int) -> dict[str, Any] | None:
        for row in self.targets.values():
            if pid in {int(p) for p in row["actions"].values()}:
                return row
        return None

    def effective(self, pid: int) -> bool:
        if self.is_full:
            return True
        state = self.states.get(pid, INHERIT)
        if state == INHERIT:
            return bool(self.info.get(pid, {}).get("role"))
        return state == ALLOW

    def _has_password(self, pid: int) -> bool:
        """فيه كلمة سر هتشتغل لو الإجراء ده مسموح (بتاعة المستخدم، أو جديدة، أو بتاعة المجموعة)."""
        info = self.info.get(pid, {})
        if pid in self.pending_passwords:
            return True
        if self.states.get(pid) == ALLOW and info.get("has_password"):
            return True
        return bool(info.get("role") and info.get("role_has_password"))

    def _needs_password(self, pid: int) -> bool:
        return self.action_of(pid) in reg.PASSWORD_ACTIONS and not self._has_password(pid)

    def set_states(self, pids: list[int], state: str) -> bool:
        """تخصيص لمجموعة صلاحيات. «سماح» لإجراء حساس من غير كلمة سر ← بيتسأل (مرة لكل إجراء)."""
        if not self.editable:
            return False
        pids = [int(p) for p in pids if int(p) in self._cells]
        if state == ALLOW:
            needed: dict[str, list[int]] = {}
            for pid in pids:
                if self.states.get(pid) != ALLOW and self._needs_password(pid):
                    needed.setdefault(self.action_of(pid), []).append(pid)
            for action, action_pids in needed.items():
                target = self.target_of(action_pids[0]) or {}
                where = target.get("target_name_ar", "") if len(action_pids) == 1 else f"{len(action_pids)} شاشة"
                password = self.ask_password(where, reg.ACTION_NAMES_AR[action])
                if password is None:
                    pids = [p for p in pids if p not in action_pids]   # من غير كلمة سر ← مايتغيّرش
                    continue
                for pid in action_pids:
                    self.pending_passwords[pid] = password
        for pid in pids:
            self.states[pid] = state
            if state != ALLOW:
                self.pending_passwords.pop(pid, None)
        self.refresh()
        return bool(pids)

    def cycle(self, pid: int) -> None:
        state = CYCLE[self.states.get(pid, INHERIT)]
        if not self.set_states([pid], state) and state == ALLOW:
            return                                   # الإلغاء = مفيش تغيير
        self.refresh()

    def reset_all(self) -> None:
        """«تصفير التخصيصات»: كله وراثة (بيتحفظ مع «حفظ»)."""
        if self.editable:
            self.set_states(list(self._cells), INHERIT)

    def changed_states(self) -> dict[int, str]:
        return {pid: s for pid, s in self.states.items() if s != self.info.get(pid, {}).get("override", INHERIT)}

    def missing_passwords(self) -> dict[str, list[int]]:
        """إجراءات حساسة هتبقى مسموحة من غير كلمة سر ← {الإجراء: [ids]}."""
        out: dict[str, list[int]] = {}
        if self.is_full:
            return out
        for pid in self._cells:
            action = self.action_of(pid)
            if action in reg.PASSWORD_ACTIONS and self.effective(pid) and not self._has_password(pid):
                out.setdefault(action, []).append(pid)
        return out

    def counts(self) -> tuple[int, int]:
        allow = sum(1 for s in self.states.values() if s == ALLOW)
        deny = sum(1 for s in self.states.values() if s == DENY)
        return allow, deny

    # ---- الرسم -------------------------------------------------------------
    def _paint(self, pid: int) -> None:
        r, c = self._cells[pid]
        item = self.table.item(r, c)
        state = self.states.get(pid, INHERIT)
        info = self.info.get(pid, {})
        on = self.effective(pid)
        key = " 🔑" if on and self.action_of(pid) in reg.PASSWORD_ACTIONS and not self.is_full else ""
        if self.is_full:
            text, color, bg = "✔", GREEN, None
        elif state == ALLOW:
            text, color, bg = "✔" + key, BLUE, OVERRIDE_BG[ALLOW]
        elif state == DENY:
            text, color, bg = "✖", RED, OVERRIDE_BG[DENY]
        else:
            text, color, bg = ("✔" + key, GREEN, None) if on else ("☐", GREY, None)
        item.setText(text)
        item.setForeground(QBrush(QColor(color)))
        item.setBackground(QBrush(QColor(bg)) if bg else QBrush())
        font = item.font()
        font.setBold(state != INHERIT or on)
        item.setFont(font)
        if self.is_full:
            tip = "مدير / صلاحية كاملة ← كل حاجة مسموحة"
        else:
            tip = (f"المجموعة: {'مسموح' if info.get('role') else 'ممنوع'}\n"
                   f"التخصيص: {STATE_AR[state]}\nالفعلية: {'مسموح' if on else 'ممنوع'}")
            if key:
                tip += "\n🔑 " + ("كلمة سر جديدة (هتتحفظ مع «حفظ»)" if pid in self.pending_passwords
                                 else "بكلمة سر" if self._has_password(pid) else "⚠ مفيش كلمة سر")
        item.setToolTip(tip)

    def refresh(self) -> None:
        self.table.setUpdatesEnabled(False)
        for pid in self._cells:
            self._paint(pid)
        self.table.setUpdatesEnabled(True)
        allow, deny = self.counts()
        if self.is_full:
            text = "المستخدم ده مدير / صلاحية كاملة — كل حاجة مسموحة له ومفيش تخصيص."
        else:
            text = f"التخصيصات: {allow + deny} ({allow} سماح · {deny} منع)"
            if self.changed_states() or self.pending_passwords:
                text += "  ·  فيه تعديلات لسه ماتحفظتش"
        self.summary.setText(text)
        self.apply_filter()

    # ---- الفلتر والبحث ----------------------------------------------------
    def _row_matches(self, key: str, mode: str) -> bool:
        pids = [int(p) for p in self.targets[key]["actions"].values()]
        if mode == "overrides":
            return any(self.states.get(p, INHERIT) != INHERIT for p in pids)
        if mode == "denied":
            return any(not self.effective(p) for p in pids)
        if mode == "allowed":
            return any(self.effective(p) for p in pids)
        return True

    def apply_filter(self) -> None:
        text = self.search.text().strip().casefold()
        mode = self.filter_combo.currentData() or "all"
        visible_groups: set[str] = set()
        for r, (kind, data) in enumerate(self._row_kind):
            if kind != "target":
                continue
            row = self.targets[data]
            show = (not text or text in row["target_name_ar"].casefold()) and self._row_matches(data, mode)
            self.table.setRowHidden(r, not show)
            if show:
                entry = self.entries[r]
                visible_groups.update(entry["gids"])
        for r, (kind, data) in enumerate(self._row_kind):
            if kind == "header":
                self.table.setRowHidden(r, data["gid"] not in visible_groups)

    # ---- الضغط -------------------------------------------------------------
    def _on_cell_clicked(self, r: int, c: int) -> None:
        item = self.table.item(r, c)
        pid = item.data(PID_ROLE) if item is not None else None
        if pid is not None and self.editable:
            self.cycle(int(pid))

    def pids_of_row(self, r: int) -> list[int]:
        kind, data = self._row_kind[r]
        if kind == "target":
            return [int(p) for p in self.targets[data]["actions"].values()]
        gid = data["gid"]
        return [int(p) for e in self.entries if e["kind"] == "target" and gid in e["gids"]
                for p in e["row"]["actions"].values()]

    def _context_menu(self, pos) -> None:  # noqa: ANN001 - Qt
        if not self.editable:
            return
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        r, c = index.row(), index.column()
        kind, _data = self._row_kind[r]
        menu = QMenu(self)
        menu.setLayoutDirection(Qt.RightToLeft)
        groups: list[tuple[str, list[int]]] = []
        item = self.table.item(r, c)
        if kind == "target" and item is not None and item.data(PID_ROLE) is not None:
            groups.append(("الخانة دي", [int(item.data(PID_ROLE))]))
        groups.append(("الشاشة كلها" if kind == "target" else "القسم كله", self.pids_of_row(r)))
        for title, pids in groups:
            for state in (INHERIT, ALLOW, DENY):
                action = menu.addAction(f"{STATE_AR[state]} — {title}")
                action.triggered.connect(lambda _c=False, p=pids, s=state: self.set_states(p, s))
            menu.addSeparator()
        menu.exec(self.table.viewport().mapToGlobal(pos))
