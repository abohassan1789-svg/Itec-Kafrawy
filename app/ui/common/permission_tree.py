"""محرر الصلاحيات — النموذج 3 «شجرة + خانات الشاشة المختارة» (اختيار المستخدم 2026-11-07).

يمين: شجرة (القسم ← القسم الفرعي ← الشاشة) مترتبة زي القائمة الجانبية بالظبط، وكل عنصر عليه خانة:
مليانة = كل الإجراءات، نص = بعضها، فاضية = ولا حاجة. العلامة على قسم = القسم كله مرة واحدة.
شمال: خانات إجراءات الشاشة المختارة بس. الإجراءات الحساسة (حذف الكل / إظهار الإجماليات) عليها 🔑:
لما تتعلّم بتتسأل عن كلمة سر جديدة، وفيه زرار لتغييرها.

المحرر واجهة بس: بيشيل حالة {permission_id: مسموح} وكلمات السر الجديدة لحد ما الشاشة تحفظ.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import permission_registry as reg
from app.ui.common.theme import GREEN, GREEN_DARK, RED, TEXT_MUTED, button_style

KIND_ROLE = Qt.UserRole + 1     # header / target
KEY_ROLE = Qt.UserRole + 2      # gid أو مفتاح الشاشة
PasswordAsker = Callable[[str, str], "str | None"]   # (الشاشة، الإجراء) ← كلمة سر أو None


class PermissionTreeEditor(QWidget):
    def __init__(self, ask_password: PasswordAsker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ask_password = ask_password
        self.entries: list[dict[str, Any]] = []
        self.targets: dict[str, dict[str, Any]] = {}       # key ← صف المصفوفة
        self.allowed: dict[int, bool] = {}
        self.password_ids: set[int] = set()                  # متحفظلها كلمة سر
        self.pending_passwords: dict[int, str] = {}          # كلمات سر جديدة لسه ماتحفظتش
        self.editable = False
        self._quiet = False
        self._items: dict[str, QTreeWidgetItem] = {}
        self._current_key: str | None = None
        self._build_ui()

    # ---- البناء ------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        top = QHBoxLayout()
        self.top_bar = top                       # الشاشة ممكن تضيف أزرار هنا (زي «مزامنة الصلاحيات»)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 بحث عن شاشة…")
        self.search.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.search.textChanged.connect(self._apply_search)
        top.addWidget(self.search, 1)
        self.check_all_button = QPushButton("تحديد الكل")
        self.check_all_button.setStyleSheet(button_style("#1A7A3C", "#166534"))
        self.check_all_button.clicked.connect(lambda: self.set_all(True))
        self.clear_all_button = QPushButton("مسح الكل")
        self.clear_all_button.setStyleSheet(button_style("#6B7280", "#4B5563"))
        self.clear_all_button.clicked.connect(lambda: self.set_all(False))
        top.addWidget(self.check_all_button)
        top.addWidget(self.clear_all_button)
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        tree_box = QGroupBox("الشاشات")
        tree_layout = QVBoxLayout(tree_box)
        tree_layout.setContentsMargins(8, 16, 8, 8)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(22)
        self.tree.setStyleSheet(
            "QTreeWidget { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:6px; font-size:15px; }"
            "QTreeWidget::item { padding:5px 2px; }"
            "QTreeWidget::item:selected { background:#DDF3E6; color:#111827; }")
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show_actions(cur))
        tree_layout.addWidget(self.tree)
        body.addWidget(tree_box, 1)                      # RTL: الشجرة يمين

        self.actions_box = QGroupBox("الإجراءات")
        actions_outer = QVBoxLayout(self.actions_box)
        actions_outer.setContentsMargins(8, 16, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:#FFFFFF; }")
        self.actions_host = QWidget()
        self.actions_layout = QVBoxLayout(self.actions_host)
        self.actions_layout.setAlignment(Qt.AlignTop)
        self.actions_layout.setContentsMargins(6, 4, 6, 4)
        self.actions_layout.setSpacing(2)
        scroll.setWidget(self.actions_host)
        actions_outer.addWidget(scroll)
        body.addWidget(self.actions_box, 1)
        root.addLayout(body, 1)
        self.summary = QLabel("")
        self.summary.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        root.addWidget(self.summary)
        self.action_checks: dict[str, QCheckBox] = {}

    # ---- تحميل -------------------------------------------------------------
    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """صفوف ``PermissionService.grouped_matrix()``."""
        self.entries = entries
        self.targets = {e["row"]["key"]: e["row"] for e in entries if e["kind"] == "target"}
        self._quiet = True
        self.tree.clear()
        self._items = {}
        parents: dict[str, QTreeWidgetItem] = {}
        for entry in entries:
            if entry["kind"] == "header":
                item = QTreeWidgetItem([f"{entry['title']}  ({entry['count']})"])
                item.setData(0, KIND_ROLE, "header")
                item.setData(0, KEY_ROLE, entry["gid"])
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                if entry["level"] == 0:
                    self.tree.addTopLevelItem(item)
                else:
                    parents[entry["gid"].split("/")[0]].addChild(item)
                parents[entry["gid"]] = item
            else:
                row = entry["row"]
                item = QTreeWidgetItem([row["target_name_ar"]])
                item.setData(0, KIND_ROLE, "target")
                item.setData(0, KEY_ROLE, row["key"])
                parents[entry["gids"][-1]].addChild(item)
                self._items[row["key"]] = item
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
        self.tree.expandAll()
        self._quiet = False
        self._refresh_states()
        first = next(iter(self._items.values()), None)
        if first is not None:
            self.tree.setCurrentItem(first)
        else:
            self._show_actions(None)

    def load(self, allowed: dict[int, bool], password_ids: set[int]) -> None:
        valid = {pid for row in self.targets.values() for pid in row["actions"].values()}
        self.allowed = {pid: bool(allowed.get(pid, False)) for pid in valid}
        self.password_ids = set(password_ids)
        self.pending_passwords = {}
        self._refresh_states()
        self._show_actions(self.tree.currentItem())

    def allowed_map(self) -> dict[int, bool]:
        return dict(self.allowed)

    def set_editable(self, editable: bool) -> None:
        self.editable = editable
        self.check_all_button.setEnabled(editable)
        self.clear_all_button.setEnabled(editable)
        self._quiet = True
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsUserCheckable if editable else Qt.NoItemFlags)
        iterator = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while iterator:
            item = iterator.pop()
            item.setFlags(flags)
            iterator.extend(item.child(i) for i in range(item.childCount()))
        self._quiet = False
        self._show_actions(self.tree.currentItem())

    # ---- الحالة ------------------------------------------------------------
    def _target_pids(self, item: QTreeWidgetItem) -> list[int]:
        if item.data(0, KIND_ROLE) == "target":
            return list(self.targets[item.data(0, KEY_ROLE)]["actions"].values())
        pids: list[int] = []
        for i in range(item.childCount()):
            pids += self._target_pids(item.child(i))
        return pids

    def _state_of(self, pids: list[int]) -> Qt.CheckState:
        on = sum(1 for pid in pids if self.allowed.get(pid))
        if pids and on == len(pids):
            return Qt.Checked
        return Qt.PartiallyChecked if on else Qt.Unchecked

    def _refresh_states(self) -> None:
        self._quiet = True
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            item.setCheckState(0, self._state_of(self._target_pids(item)))
            stack.extend(item.child(i) for i in range(item.childCount()))
        self._quiet = False
        total = len(self.allowed)
        on = sum(1 for v in self.allowed.values() if v)
        self.summary.setText(f"الصلاحيات الممنوحة: {on} من {total}")

    def _set_pids(self, pids: list[int], value: bool) -> bool:
        """بيعلّم/يشيل مجموعة صلاحيات. لو فيها إجراء حساس جديد بيتسأل عن كلمة سر (مرة لكل إجراء)."""
        if value:
            needed: dict[str, list[int]] = {}
            for pid in pids:
                action = self._action_of(pid)
                if (action in reg.PASSWORD_ACTIONS and not self.allowed.get(pid)
                        and pid not in self.password_ids and pid not in self.pending_passwords):
                    needed.setdefault(action, []).append(pid)
            for action, action_pids in needed.items():
                where = (self._target_name_of(action_pids[0]) if len(action_pids) == 1
                         else f"{len(action_pids)} شاشة")
                password = self.ask_password(where, reg.ACTION_NAMES_AR[action])
                if password is None:
                    pids = [p for p in pids if p not in action_pids]   # من غير كلمة سر ← مايتعلّمش
                    continue
                for pid in action_pids:
                    self.pending_passwords[pid] = password
        for pid in pids:
            self.allowed[pid] = value
            if not value:
                self.pending_passwords.pop(pid, None)
        return bool(pids)

    def _action_of(self, pid: int) -> str:
        for row in self.targets.values():
            for action, action_pid in row["actions"].items():
                if action_pid == pid:
                    return action
        return ""

    def _target_name_of(self, pid: int) -> str:
        for row in self.targets.values():
            if pid in row["actions"].values():
                return row["target_name_ar"]
        return ""

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._quiet:
            return
        if not self.editable:
            self._refresh_states()
            return
        value = item.checkState(0) == Qt.Checked
        self._set_pids(self._target_pids(item), value)
        self._refresh_states()
        self._show_actions(self.tree.currentItem())

    def set_all(self, value: bool) -> None:
        if not self.editable:
            return
        self._set_pids(list(self.allowed), value)
        self._refresh_states()
        self._show_actions(self.tree.currentItem())

    # ---- خانات الإجراءات ---------------------------------------------------
    def _clear_actions(self) -> None:
        while self.actions_layout.count():
            child = self.actions_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.hide()                    # deleteLater بيتأخر ← من غير hide القديم يفضل باين تحت الجديد
                widget.deleteLater()
        self.action_checks = {}

    def _show_actions(self, item: QTreeWidgetItem | None) -> None:
        self._clear_actions()
        if item is None or item.data(0, KIND_ROLE) != "target":
            self._current_key = None
            self.actions_box.setTitle("الإجراءات")
            hint = QLabel("اختار شاشة من الشجرة عشان تشوف إجراءاتها.")
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
            self.actions_layout.addWidget(hint)
            return
        key = item.data(0, KEY_ROLE)
        row = self.targets[key]
        self._current_key = key
        self.actions_box.setTitle(f"إجراءات: {row['target_name_ar']}")
        for action, pid in row["actions"].items():
            line = QHBoxLayout()
            line.setContentsMargins(0, 3, 0, 3)
            line.setSpacing(8)
            check = QCheckBox(reg.ACTION_NAMES_AR.get(action, action))
            check.setChecked(bool(self.allowed.get(pid)))
            check.setEnabled(self.editable)
            check.setStyleSheet("QCheckBox { font-size:15px; font-weight:700; }")
            check.toggled.connect(lambda on, p=pid: self._on_action_toggled(p, on))
            line.addWidget(check)
            self.action_checks[action] = check
            if action in reg.PASSWORD_ACTIONS:
                line.addWidget(self._password_badge(pid))
                if self.editable and self.allowed.get(pid):
                    change = QPushButton("تغيير كلمة السر")
                    change.setToolTip("كلمة سر جديدة للإجراء ده (بتتحفظ مع «حفظ»)")
                    change.setStyleSheet(button_style("#6D28D9", "#5B21B6"))
                    change.setMinimumWidth(change.fontMetrics().horizontalAdvance(change.text()) + 36)
                    change.clicked.connect(lambda _c=False, p=pid: self.change_password(p))
                    line.addWidget(change)
            line.addStretch(1)
            holder = QWidget()
            holder.setLayout(line)
            self.actions_layout.addWidget(holder)

    def _password_badge(self, pid: int) -> QLabel:
        if not self.allowed.get(pid):
            text, color = "🔑 بكلمة سر", TEXT_MUTED
        elif pid in self.pending_passwords:
            text, color = "🔑 كلمة سر جديدة", GREEN
        elif pid in self.password_ids:
            text, color = "🔑 كلمة السر متحطة", GREEN_DARK
        else:
            text, color = "⚠ مفيش كلمة سر", RED
        label = QLabel(text)
        label.setStyleSheet(f"color:{color}; font-weight:800;")
        label.setMinimumWidth(label.sizeHint().width())      # ماتتقصّش جنب زرار التغيير
        if pid in self.pending_passwords:
            label.setToolTip("هتتحفظ مع «حفظ»")
        return label

    def _on_action_toggled(self, pid: int, on: bool) -> None:
        if not self.editable:
            return
        self._set_pids([pid], on)
        self._refresh_states()
        self._show_actions(self.tree.currentItem())

    def change_password(self, pid: int) -> None:
        if not self.editable or not self.allowed.get(pid):
            return
        password = self.ask_password(self._target_name_of(pid), reg.ACTION_NAMES_AR[self._action_of(pid)])
        if password is not None:
            self.pending_passwords[pid] = password
            self._show_actions(self.tree.currentItem())

    def missing_passwords(self) -> dict[str, list[int]]:
        """إجراءات حساسة متعلّم عليها من غير كلمة سر (قديمة ولا جديدة) ← {الإجراء: [ids]}."""
        out: dict[str, list[int]] = {}
        for pid, on in self.allowed.items():
            action = self._action_of(pid)
            if on and action in reg.PASSWORD_ACTIONS and pid not in self.password_ids \
                    and pid not in self.pending_passwords:
                out.setdefault(action, []).append(pid)
        return out

    # ---- البحث -------------------------------------------------------------
    def _apply_search(self) -> None:
        text = self.search.text().strip().casefold()

        def visit(item: QTreeWidgetItem) -> bool:
            if item.data(0, KIND_ROLE) == "target":
                visible = not text or text in item.text(0).casefold()
            else:
                visible = False
                for i in range(item.childCount()):
                    visible = visit(item.child(i)) or visible
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
