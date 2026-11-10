"""الواجهة الرئيسية — النموذج 4 «شريط عريض (أيقونة جنب الاسم)» (اختيار المستخدم 2026-11-07).

منقولة من «الواجهة التانية» بتاعة Hr GAD (``main_window_v2``) كـ class لوحده:
* **شريط أخضر غامق يمين** فيه البراند، 🏠 الرئيسية، وزرار لكل قسم (أيقونة + اسم في سطر واحد)،
  و«النظام والصلاحيات» تحت لوحده، و«تسجيل الخروج» آخر الشريط.
* **قائمة منبثقة بيضا** بتطلع جنب الشريط لما الماوس يعدّي على القسم (أو بالضغط): عنوان + خط أخضر +
  أقسام فرعية + الشاشات. بتقفل بـ Esc أو الضغط برّه أو لما الماوس يخرج.
* **شريط علوي أبيض** (66): عنوان الصفحة يمين | صورة المستخدم + الاسم + المهنة | الساعة والتاريخ.
* **الرئيسية**: لوجو ITEC في النص و«A-H CODE» صغير تحت على الشمال (زي Hr GAD).
* كل شاشة بتفتح في **شباك لوحدها مكبّر**، ولو مفتوحة بيرجع لها. القائمة بتظهر اللي ليك عليه «عرض» بس.
* **مفيش ✕** — القفل من «تسجيل الخروج» بس، ولو فيه شاشة لسه في «جديد/تعديل» بيمنع الخروج ويقول أنهي.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_TITLE
from app.security.session import SESSION
from app.services.auth_service import AuthService
from app.services.navigation_service import menu_tree, view_code
from app.ui.common.home_page import COMPANY_LOGO, HomePage
from app.ui.common.theme import BORDER, BORDER_CARD, GREEN, GREEN_DARK, TEXT, TEXT_MUTED
from app.ui.common.user_header import UserHeader
from app.ui.screen_registry import SCREEN_FACTORIES, ScreenFactory

HOME = "home"
RAIL_WIDTH = 220          # النموذج 4: الاسم جنب الأيقونة
TOP_BAR_HEIGHT = 66
POPUP_WIDTH = 340
OPEN_DELAY_MS = 120       # الماوس يعدّي على القسم ← القائمة تطلع بعد لحظة
CLOSE_DELAY_MS = 300      # الماوس يخرج ← تقفل بعد لحظة (عشان ماتقفلش وهو رايح لها)
LOGOUT_TEXT = "تسجيل الخروج"
EDITING_MODES = ("new", "edit")
NO_PERMISSION = "ليس لديك صلاحية لفتح هذه الشاشة"

_RAIL_STYLE = f"""
QFrame#rail {{ background:{GREEN_DARK}; }}
QLabel#railBrand {{ color:#FFFFFF; font-size:22px; font-weight:900; background:transparent; padding:4px 0 2px 0; }}
QLabel#railSub {{ color:#BBF7D0; font-size:13px; font-weight:700; background:transparent; padding-bottom:6px; }}
QScrollArea#railScroll, QWidget#railHost {{ background:transparent; border:none; }}
QScrollArea#railScroll QScrollBar:vertical {{ background:transparent; width:4px; margin:0; }}
QScrollArea#railScroll QScrollBar::handle:vertical {{ background:rgba(255,255,255,0.28); border-radius:2px; }}
QScrollArea#railScroll QScrollBar::add-line:vertical, QScrollArea#railScroll QScrollBar::sub-line:vertical {{ height:0; }}
QPushButton#railBtn {{ background:transparent; border:1px solid transparent; border-radius:10px; }}
QPushButton#railBtn:hover {{ background:rgba(255,255,255,0.12); }}
QPushButton#railBtn[open="true"] {{ background:rgba(255,255,255,0.18); border-color:rgba(255,255,255,0.55); }}
QPushButton#railBtn[active="true"] {{ background:#FFFFFF; border-color:#FFFFFF; }}
QLabel#railIcon {{ color:#FFFFFF; font-size:20px; background:transparent; }}
QLabel#railText {{ color:#D6F5E0; font-size:16px; font-weight:800; background:transparent; }}
QLabel#railText[active="true"] {{ color:{GREEN_DARK}; font-weight:900; }}
QLabel#railSoon {{ color:rgba(255,255,255,0.55); font-size:11px; font-weight:700; background:transparent; }}
QFrame#railLine {{ background:rgba(255,255,255,0.30); }}
QPushButton#logout {{ background:rgba(220,38,38,0.18); border:1px solid rgba(255,255,255,0.35); border-radius:10px; }}
QPushButton#logout:hover {{ background:#DC2626; border-color:#DC2626; }}
QLabel#logoutIcon {{ color:#FFFFFF; font-size:19px; background:transparent; }}
QLabel#logoutText {{ color:#FFFFFF; font-size:16px; font-weight:900; background:transparent; }}
"""

_POPUP_STYLE = f"""
QFrame#popup {{ background:#FFFFFF; border:none; border-left:1px solid {BORDER}; border-right:1px solid {BORDER}; }}
QLabel#popTitle {{ color:{GREEN_DARK}; font-size:17px; font-weight:900; background:transparent; }}
QPushButton#popClose {{ background:#F1F5F9; color:#475569; border:none; border-radius:8px; font-size:16px;
    font-weight:900; min-width:28px; max-width:28px; min-height:28px; max-height:28px; }}
QPushButton#popClose:hover {{ background:#FEE2E2; color:#B91C1C; }}
QScrollArea#popScroll, QWidget#popBody {{ background:transparent; border:none; }}
QLabel#popSub {{ color:{TEXT_MUTED}; font-size:15px; font-weight:900; background:transparent; padding:10px 6px 2px 6px; }}
QLabel#popEmpty {{ color:{TEXT_MUTED}; font-size:15px; font-weight:700; background:transparent; padding:14px 6px; }}
QPushButton#popItem {{ text-align:left; color:{TEXT}; background:transparent; border:1px solid transparent;
    border-radius:10px; padding:8px 12px; font-size:16px; font-weight:800; }}
QPushButton#popItem:hover {{ background:#E8F5EC; color:{GREEN_DARK}; border-color:#BBE5C8; }}
QPushButton#popItem[active="true"] {{ background:{GREEN}; color:#FFFFFF; }}
"""


def _set_flag(widget: QWidget, name: str, value: bool) -> None:
    """يغيّر خاصية الحالة ويعيد تطبيق الستايل على العنصر ده بس."""
    if bool(widget.property(name)) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _info(parent: QWidget, title: str, text: str, icon=QMessageBox.Information) -> None:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton("تمام", QMessageBox.AcceptRole)
    box.exec()


class MainWindow(QMainWindow):
    def __init__(self, auth_service: AuthService | None = None,
                 factories: dict[str, ScreenFactory] | None = None,
                 photo_provider: Callable[[], bytes | None] | None = None) -> None:
        super().__init__()
        self.auth_service = auth_service or AuthService()
        self.factories = SCREEN_FACTORIES if factories is None else factories
        self._photo_provider = photo_provider or self._load_photo
        self.open_windows: dict[str, QWidget] = {}
        self._rail_buttons: dict[str, QPushButton] = {}
        self._rail_texts: dict[str, QLabel] = {}
        self._popup: QFrame | None = None
        self._popup_section: str | None = None
        self._popup_buttons: dict[str, QPushButton] = {}
        self._active_key: str | None = None
        self._hover_rail: str | None = None
        self._hover_popup = False
        self._pending_section: str | None = None
        self._filter_on = False
        self._quitting = False
        self.tree = menu_tree(SESSION.can)
        self._by_id = {sec["id"]: sec for sec in self.tree}

        self.setWindowTitle(APP_TITLE)
        self.setLayoutDirection(Qt.RightToLeft)
        if COMPANY_LOGO.exists():
            self.setWindowIcon(QIcon(str(COMPANY_LOGO)))
        self.setMinimumSize(1000, 640)
        self._hide_close_button()

        self._open_timer = QTimer(self, singleShot=True, interval=OPEN_DELAY_MS)
        self._open_timer.timeout.connect(self._open_pending)
        self._close_timer = QTimer(self, singleShot=True, interval=CLOSE_DELAY_MS)
        self._close_timer.timeout.connect(self._close_if_left)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._rail = self._build_rail()
        root.addWidget(self._rail)                 # RTL: أول عنصر = يمين
        root.addWidget(self._build_content(), 1)
        self._highlight(HOME)

    # ---- من غير ✕ ---------------------------------------------------------
    def _hide_close_button(self) -> None:
        flags = self.windowFlags() | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowMinMaxButtonsHint
        self.setWindowFlags(flags & ~Qt.WindowCloseButtonHint)

    # ---- الشريط ------------------------------------------------------------
    def _build_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(RAIL_WIDTH)
        rail.setStyleSheet(_RAIL_STYLE)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(4)
        brand = QLabel(APP_NAME)
        brand.setObjectName("railBrand")
        brand.setAlignment(Qt.AlignCenter)
        sub = QLabel("برنامج الحسابات")
        sub.setObjectName("railSub")
        sub.setAlignment(Qt.AlignCenter)
        layout.addWidget(brand)
        layout.addWidget(sub)

        home = self._make_rail_button(HOME, "🏠", "الرئيسية", "الصفحة الرئيسية")
        home.clicked.connect(self.show_home)
        layout.addWidget(home)

        scroll = QScrollArea()
        scroll.setObjectName("railScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("railHost")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 6, 0, 0)
        host_layout.setSpacing(3)
        bottom = [sec for sec in self.tree if sec["bottom"]]
        for sec in [s for s in self.tree if not s["bottom"]]:
            host_layout.addWidget(self._section_button(sec))
        host_layout.addStretch(1)
        for sec in bottom:                         # «النظام والصلاحيات» تحت خالص لوحده
            host_layout.addWidget(self._section_button(sec))
        scroll.setWidget(host)
        scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(scroll, 1)

        line = QFrame()
        line.setObjectName("railLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.logout_button = self._make_logout_button()
        layout.addWidget(self.logout_button)
        return rail

    def _section_button(self, sec: dict) -> QPushButton:
        tip = sec["title"] + (" — قريباً" if sec["coming_soon"] else f" — {sec['count']} شاشة")
        button = self._make_rail_button(sec["id"], sec["icon"], sec["title"], tip,
                                        soon=sec["coming_soon"])
        button.clicked.connect(lambda _c=False, sid=sec["id"]: self._toggle_popup(sid))
        button.installEventFilter(self)
        return button

    def _make_rail_button(self, rid: str, icon: str, text: str, tip: str, soon: bool = False) -> QPushButton:
        button = QPushButton()
        button.setObjectName("railBtn")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(48)
        button.setToolTip(tip)
        button.setAccessibleName(text)
        row = QHBoxLayout(button)
        row.setContentsMargins(10, 2, 10, 2)
        row.setSpacing(10)
        icon_label = QLabel(icon)
        icon_label.setObjectName("railIcon")
        icon_label.setFixedWidth(28)
        icon_label.setAlignment(Qt.AlignCenter)
        text_label = QLabel(text)
        text_label.setObjectName("railText")
        labels = [icon_label, text_label]
        row.addWidget(icon_label)
        row.addWidget(text_label, 1)
        if soon:
            soon_label = QLabel("قريباً")
            soon_label.setObjectName("railSoon")
            row.addWidget(soon_label)
            labels.append(soon_label)
        for label in labels:
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._rail_buttons[rid] = button
        self._rail_texts[rid] = text_label
        return button

    def _make_logout_button(self) -> QPushButton:
        button = QPushButton()
        button.setObjectName("logout")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(48)
        button.setToolTip(f"{LOGOUT_TEXT} — بيقفل البرنامج")
        button.setAccessibleName(LOGOUT_TEXT)
        row = QHBoxLayout(button)
        row.setContentsMargins(10, 2, 10, 2)
        row.setSpacing(10)
        for text, name in (("🚪", "logoutIcon"), (LOGOUT_TEXT, "logoutText")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(label, 0 if name == "logoutIcon" else 1)
        button.clicked.connect(self.request_logout)
        return button

    # ---- المحتوى (الشريط العلوي + الرئيسية) -------------------------------
    def _build_content(self) -> QWidget:
        area = QWidget()
        box = QVBoxLayout(area)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(TOP_BAR_HEIGHT)
        bar.setStyleSheet(f"QFrame#topBar {{ background:#FFFFFF; border-bottom:1px solid {BORDER_CARD}; }}")
        line = QHBoxLayout(bar)
        line.setContentsMargins(18, 6, 18, 6)
        line.setSpacing(14)
        self.page_title = QLabel("🏠  الرئيسية")
        self.page_title.setStyleSheet(f"color:{GREEN_DARK}; font-size:20px; font-weight:900; background:transparent;")
        line.addWidget(self.page_title)
        line.addStretch(1)
        self.user_header = UserHeader(lambda: SESSION.user, self._photo_provider)
        line.addWidget(self.user_header)
        box.addWidget(bar)
        self.home_page = HomePage()
        box.addWidget(self.home_page, 1)
        return area

    def _load_photo(self) -> bytes | None:
        if SESSION.user_id is None:
            return None
        photo, _mime = self.auth_service.repository.get_user_photo(SESSION.user_id)
        return photo

    def refresh_user_header(self) -> None:
        """بعد ما المستخدم الداخل يعدّل اسمه / مهنته / صورته."""
        self.user_header.refresh()

    # ---- فتح الشاشات -------------------------------------------------------
    def show_home(self) -> None:
        self.close_popup()
        self._highlight(HOME)
        self.raise_()
        self.activateWindow()

    def module_of(self, key: str) -> str | None:
        from app.services import permission_registry as reg

        for s in reg.SECTIONS:
            if any(t.code == key for t in s.targets()):
                return s.id
        return None

    def open_screen(self, key: str) -> QWidget | None:
        """يفتح الشاشة في شباك لوحدها مكبّر (أو يرجع لها لو مفتوحة) بعد التحقق من صلاحية «عرض»."""
        self.close_popup()
        module = self.module_of(key)
        if module is None or not SESSION.can(view_code(module, key)):
            _info(self, "الصلاحيات", NO_PERMISSION, QMessageBox.Warning)
            return None
        window = self.open_windows.get(key)
        if window is not None and not window.isVisible():
            self.open_windows.pop(key, None)
            window = None
        if window is None:
            factory = self.factories.get(key)
            if factory is None:
                _info(self, "قريباً", "الشاشة دي لسه بتتبني — هتشتغل في الخطوة الجاية.")
                return None
            window = factory()
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(lambda _o=None, k=key: self.open_windows.pop(k, None))
            self.open_windows[key] = window
            window.showMaximized()
        else:
            window.showMaximized()
            window.raise_()
            window.activateWindow()
        self._highlight(key)
        return window

    # ---- القائمة المنبثقة --------------------------------------------------
    def _ensure_popup(self) -> QFrame:
        if self._popup is not None:
            return self._popup
        popup = QFrame(self.centralWidget())
        popup.setObjectName("popup")
        popup.setStyleSheet(_POPUP_STYLE)
        popup.setFixedWidth(POPUP_WIDTH)
        shadow = QGraphicsDropShadowEffect(popup)
        shadow.setBlurRadius(28)
        shadow.setOffset(-6, 0)
        shadow.setColor(QColor(15, 23, 42, 60))
        popup.setGraphicsEffect(shadow)
        box = QVBoxLayout(popup)
        box.setContentsMargins(12, 12, 12, 10)
        box.setSpacing(6)
        head = QHBoxLayout()
        self._popup_title = QLabel("")
        self._popup_title.setObjectName("popTitle")
        head.addWidget(self._popup_title, 1)
        close = QPushButton("✕")
        close.setObjectName("popClose")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("قفل القائمة (Esc)")
        close.clicked.connect(self.close_popup)
        head.addWidget(close)
        box.addLayout(head)
        line = QFrame()
        line.setFixedHeight(2)
        line.setStyleSheet(f"background:{GREEN};")
        box.addWidget(line)
        scroll = QScrollArea()
        scroll.setObjectName("popScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        box.addWidget(scroll, 1)
        self._popup_scroll = scroll
        popup.installEventFilter(self)
        popup.hide()
        self._popup = popup
        return popup

    def _build_popup_body(self, sec: dict) -> QWidget:
        body = QWidget()
        body.setObjectName("popBody")
        lines = QVBoxLayout(body)
        lines.setContentsMargins(0, 2, 0, 0)
        lines.setSpacing(3)
        self._popup_buttons = {}
        if sec["coming_soon"]:
            empty = QLabel("الشاشات بتاعة القسم ده هتتضاف في المراحل الجاية.")
            empty.setObjectName("popEmpty")
            empty.setWordWrap(True)
            lines.addWidget(empty)
        for group in sec["groups"]:
            if group["title"]:
                sub = QLabel(f"{group['icon']}  {group['title']}".strip())
                sub.setObjectName("popSub")
                lines.addWidget(sub)
            for item in group["items"]:
                button = QPushButton(f"{item['icon']}   {item['title']}")
                button.setObjectName("popItem")
                button.setCursor(Qt.PointingHandCursor)
                if item["key"] == self._active_key:
                    button.setProperty("active", True)
                button.clicked.connect(lambda _c=False, k=item["key"]: self.open_screen(k))
                lines.addWidget(button)
                self._popup_buttons[item["key"]] = button
        lines.addStretch(1)
        return body

    def _place_popup(self) -> None:
        popup, central = self._popup, self.centralWidget()
        if popup is None or central is None:
            return
        rail = self._rail.geometry()
        # جنب الشريط من الناحية التانية (الشريط يمين ← القائمة على شماله)، بطول الشباك.
        x = rail.x() - POPUP_WIDTH if rail.center().x() > central.width() // 2 else rail.right() + 1
        popup.setGeometry(QRect(x, 0, POPUP_WIDTH, central.height()))

    def show_popup(self, section_id: str) -> None:
        sec = self._by_id.get(section_id)
        if sec is None:
            return
        self._open_timer.stop()
        self._close_timer.stop()
        popup = self._ensure_popup()
        if self._popup_section != section_id or not popup.isVisible():
            self._popup_title.setText(f"{sec['icon']}  {sec['title']}")
            old = self._popup_scroll.takeWidget()
            if old is not None:
                old.deleteLater()
            self._popup_scroll.setWidget(self._build_popup_body(sec))
        for sid, button in self._rail_buttons.items():
            _set_flag(button, "open", sid == section_id)
        self._popup_section = section_id
        self._place_popup()
        popup.show()
        popup.raise_()
        self._set_app_filter(True)

    def close_popup(self) -> None:
        self._open_timer.stop()
        self._close_timer.stop()
        self._pending_section = None
        self._hover_popup = False
        if self._popup is not None:
            self._popup.hide()
        self._popup_section = None
        for button in self._rail_buttons.values():
            _set_flag(button, "open", False)
        self._set_app_filter(False)

    def popup_open(self) -> bool:
        return self._popup is not None and self._popup.isVisible()

    def _toggle_popup(self, section_id: str) -> None:
        if self.popup_open() and self._popup_section == section_id:
            self.close_popup()
        else:
            self.show_popup(section_id)

    def _open_pending(self) -> None:
        if self._pending_section and self._hover_rail == self._pending_section:
            self.show_popup(self._pending_section)
        self._pending_section = None

    def _close_if_left(self) -> None:
        if self._hover_popup or (self._hover_rail and self._hover_rail == self._popup_section):
            return
        if self.popup_open():
            pos = QCursor.pos()
            if self._popup.rect().contains(self._popup.mapFromGlobal(pos)):
                return
            current = self._rail_buttons.get(self._popup_section or "")
            if current is not None and current.rect().contains(current.mapFromGlobal(pos)):
                return
        self.close_popup()

    # ---- الماوس والكيبورد --------------------------------------------------
    def _set_app_filter(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None or on == self._filter_on:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._filter_on = on

    def _section_of_rail(self, obj) -> str | None:  # noqa: ANN001 - QObject
        for sid, button in self._rail_buttons.items():
            if obj is button and sid != HOME:
                return sid
        return None

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001, N802 - Qt API
        etype = event.type()
        if etype in (QEvent.Enter, QEvent.Leave):
            sid = self._section_of_rail(obj)
            if sid is not None:
                self._rail_hover(sid, etype == QEvent.Enter)
            elif obj is self._popup:
                self._popup_hover(etype == QEvent.Enter)
        elif self._filter_on and self.popup_open():
            if etype == QEvent.KeyPress and event.key() == Qt.Key_Escape and self._in_this_window(obj):
                self.close_popup()
                return True
            if etype == QEvent.MouseButtonPress and self._in_this_window(obj) and self._outside(event):
                self.close_popup()
        return super().eventFilter(obj, event)

    def _in_this_window(self, obj) -> bool:  # noqa: ANN001 - QObject
        return isinstance(obj, QWidget) and obj.window() is self

    def _outside(self, event) -> bool:  # noqa: ANN001 - QMouseEvent
        pos = event.globalPosition().toPoint()
        widgets = [self._popup, *self._rail_buttons.values()]
        return not any(w.rect().contains(w.mapFromGlobal(pos)) for w in widgets if w is not None)

    def _rail_hover(self, section_id: str, entered: bool) -> None:
        if entered:
            self._hover_rail = section_id
            self._close_timer.stop()
            if self.popup_open():
                if self._popup_section != section_id:
                    self.show_popup(section_id)   # القائمة مفتوحة ← تتنقل على طول
            else:
                self._pending_section = section_id
                self._open_timer.start()
        else:
            if self._hover_rail == section_id:
                self._hover_rail = None
            if self._pending_section == section_id:
                self._open_timer.stop()
                self._pending_section = None
            if self.popup_open():
                self._close_timer.start()

    def _popup_hover(self, entered: bool) -> None:
        self._hover_popup = entered
        if entered:
            self._close_timer.stop()
        elif self.popup_open():
            self._close_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt API
        super().resizeEvent(event)
        if self.popup_open():
            self._place_popup()

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() == QEvent.WindowDeactivate and self._popup is not None:
            self.close_popup()
        return result

    # ---- التلوين -----------------------------------------------------------
    def _highlight(self, key: str) -> None:
        """زرار القسم اللي فيه الشاشة الحالية بيبقى أبيض (و🏠 للرئيسية)."""
        self._active_key = key
        target = HOME if key == HOME else self.module_of(key)
        for rid, button in self._rail_buttons.items():
            _set_flag(button, "active", rid == target)
            _set_flag(self._rail_texts[rid], "active", rid == target)
        for item_key, button in self._popup_buttons.items():
            _set_flag(button, "active", item_key == key)

    def active_rail(self) -> str | None:
        return next((rid for rid, b in self._rail_buttons.items() if b.property("active")), None)

    # ---- تسجيل الخروج ------------------------------------------------------
    def editing_screens(self) -> list[str]:
        """أسماء الشاشات المفتوحة اللي لسه في «جديد» أو «تعديل»."""
        return [w.windowTitle() for w in self.open_windows.values()
                if w.isVisible() and getattr(w, "mode", "view") in EDITING_MODES]

    def confirm_logout(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(LOGOUT_TEXT)
        box.setText("عايز تسجّل خروج؟")
        yes = box.addButton("نعم", QMessageBox.YesRole)
        no = box.addButton("لا", QMessageBox.NoRole)
        box.setDefaultButton(no)
        box.setEscapeButton(no)
        box.exec()
        return box.clickedButton() is yes

    def request_logout(self) -> bool:
        """التأكد إن مفيش شاشة بتتعدل ← السؤال ← تسجيل وقت الخروج ← «تم تسجيل الخروج بنجاح» ← القفل."""
        self.close_popup()
        editing = self.editing_screens()
        if editing:
            _info(self, LOGOUT_TEXT,
                  "الشاشات دي لسه فيها «جديد» أو «تعديل» ومتحفظتش:\n\n• " + "\n• ".join(editing)
                  + "\n\nاحفظ أو الغي الأول، وبعدين سجّل خروج.", QMessageBox.Warning)
            return False
        if not self.confirm_logout():
            return False
        self.auth_service.logout()
        _info(self, LOGOUT_TEXT, "تم تسجيل الخروج بنجاح")
        self.quit_after_logout()
        return True

    def quit_after_logout(self) -> None:
        self._quitting = True
        for window in list(self.open_windows.values()):
            window.close()
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt
        # القفل من الويندوز (Alt+F4، شريط المهام) ممنوع — من «تسجيل الخروج» بس؛ قفل الجهاز نفسه مسموح.
        app = QApplication.instance()
        if not self._quitting and not (app is not None and app.isSavingSession()):
            event.ignore()
            QTimer.singleShot(0, lambda: _info(
                self, LOGOUT_TEXT, f"عشان تقفل البرنامج دوس «{LOGOUT_TEXT}» آخر الشريط الأخضر."))
            return
        self.close_popup()
        super().closeEvent(event)
