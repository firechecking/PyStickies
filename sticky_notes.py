import os, sys, json, traceback
from datetime import datetime
import markdown
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QTextEdit,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QLabel,
    QMenu,
    QAction,
    QColorDialog,
    QSystemTrayIcon,
    QTextBrowser,
)
from PyQt5.QtCore import (
    Qt,
    QTimer,
    QPropertyAnimation,
    QRect,
    QPoint,
    QSettings,
    pyqtSignal,
    QObject,
)
from PyQt5.QtGui import (
    QColor,
    QPalette,
    QFont,
    QIcon,
    QCursor,
    QMouseEvent,
    QKeySequence,
    QScreen,
    QPixmap,
)

# macOS specific imports for all-desktop support
try:
    import objc
    from AppKit import NSStatusWindowLevel, NSFloatingWindowLevel
    from Cocoa import NSApplication, NSApp, NSWindowCollectionBehaviorCanJoinAllSpaces

    MACOS_AVAILABLE = True
except ImportError:
    MACOS_AVAILABLE = False


class StickyNoteManager(QObject):
    note_closed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.notes = {}
        self._loading = False  # 防止加载时的循环
        self.settings_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "notes_data.json"
        )
        self.load_notes()

    def load_notes(self):
        # 从JSON文件加载数据，而不是QSettings
        notes_data = {}
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    notes_data = json.load(f)
                print(f"Loaded {len(notes_data)} notes from {self.settings_file}")
            else:
                print("No saved notes found, starting fresh")
        except Exception as e:
            print(f"加载便签失败: {e}")
            traceback.print_exc()
        for note_id, note_data in notes_data.items():
            note = self.display_note(
                note_id=note_id,
                content=note_data["content"],
                position=QPoint(note_data["position"][0], note_data["position"][1]),
                size=note_data["size"],
                color=QColor(note_data.get("color", "#FFF9C4")),
                opacity=note_data.get("opacity", 85),
                screen_index=note_data.get("screen_index", None),
            )
            note.is_expanded = note_data.get("is_expanded", False)
            note.edge_snapped = note_data.get("edge_snapped", None)

    def create_new_note(self):
        screens = QApplication.screens()
        current_screen = QApplication.screenAt(QCursor.pos())
        screen_index = screens.index(current_screen) if current_screen else 0

        screen_geo = screens[screen_index].geometry()
        position = QPoint(screen_geo.x() + 100, screen_geo.y() + 100)

        return self.display_note(position=position, screen_index=screen_index)

    def display_note(
        self,
        note_id=None,
        content="",
        position=None,
        size=None,
        color=None,
        opacity=None,
        screen_index=None,
    ):
        if note_id is None:
            note_id = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if screen_index is None:
            screens = QApplication.screens()
            current_screen = QApplication.screenAt(QCursor.pos())
            screen_index = screens.index(current_screen) if current_screen else 0

        self._loading = True
        try:
            note = StickyNote(note_id, self)

            if position:
                note.move(position)
            if size:
                if isinstance(size, list) and len(size) == 2:
                    note.resize(size[0], size[1])
                else:
                    note.resize(size)
            if content:
                # 在加载期间临时禁用自动保存
                old_auto_save = note.text_edit.blockSignals(True)
                note.set_content(content, replace=True)
                note.text_edit.blockSignals(old_auto_save)
            if color:
                note.set_color(color)
            if opacity is not None:
                note.set_opacity(opacity)
        finally:
            self._loading = False

        note.screen_index = screen_index
        self.notes[note_id] = note
        note.show()
        return note

    def save_notes(self):
        """保存便签数据（按基础ID去重）"""
        notes_data = {}
        processed_base_ids = set()

        try:
            for note_id, note in self.notes.items():
                # 保存所有便签，不管是否可见
                base_id = note_id.split("_screen_")[0]
                if base_id not in processed_base_ids:
                    try:
                        screen = QApplication.screenAt(note.pos())
                        screen_index = (
                            QApplication.screens().index(screen) if screen else 0
                        )
                        screen_geo = QApplication.screens()[screen_index].geometry()

                        # 计算相对屏幕坐标
                        rel_x = note.x() - screen_geo.x()
                        rel_y = note.y() - screen_geo.y()

                        notes_data[base_id] = {
                            "content": str(note.get_content() or ""),
                            "position": [int(rel_x), int(rel_y)],
                            "size": [int(note.width()), int(note.height())],
                            "color": str(note.color.name() or "#FFF9C4"),
                            "opacity": int(note.opacity_slider.value() or 85),
                            "screen_index": int(screen_index),
                            "is_expanded": bool(note.is_expanded),
                            "edge_snapped": (
                                str(note.edge_snapped)
                                if note.edge_snapped is not None
                                else None
                            ),
                        }
                        processed_base_ids.add(base_id)
                    except Exception as e:
                        print(f"保存便签时出错: {e}")

            # 直接写入JSON文件
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(notes_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"Critical file save error: {e}")
            traceback.print_exc()

    def close_note(self, note_id, save=True):
        if note_id in self.notes:
            del self.notes[note_id]
            if save:
                self.save_notes()

    def cleanup_and_exit(self):
        """程序退出前保存所有数据"""
        self.save_notes()
        print("Data saved before exit")

    def show_all_notes(self):
        for note in self.notes.values():
            note.show()
            note.raise_()
            note.activateWindow()

    def hide_all_notes(self):
        for note in self.notes.values():
            note.hide()

    def delete_all_notes(self):
        """删除所有便签"""
        note_ids = list(self.notes.keys())
        for note_id in note_ids:
            self.notes[note_id].close()
        self.notes.clear()
        self.save_notes()

    def duplicate_note(self, source_note):
        """复制便签"""
        content = source_note.get_content()
        color = source_note.color
        opacity = source_note.opacity_slider.value()
        position = QPoint(source_note.x() + 30, source_note.y() + 30)

        return self.display_note(
            content=content,
            position=position,
            color=color,
            opacity=opacity,
            screen_index=source_note.screen_index,
        )


class StickyNote(QMainWindow):
    def __init__(self, note_id, manager):
        super().__init__()
        self.note_id = note_id
        self.manager = manager
        self.screen_index = 0
        self.color = QColor("#FFF9C4")
        self.is_expanded = True
        self.edge_snapped = None
        self.drag_pos = None
        self.dragging = False
        self.resizing = False
        self.expand_windown_size = [300, 200]
        self.collapse_windown_size = [50, 30]
        self.full_content = ""
        self.show_preview = False

        self.init_ui()
        self.setup_edge_detection()
        self.setup_shortcuts()
        self.setup_context_menu()

    def init_ui(self):
        # 简化置顶设置，确保有效
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(20, 10)  # 最小尺寸
        self.setMaximumSize(600, 400)  # 最大尺寸

        # Main widget
        self.main_widget = QWidget()
        self.main_widget.setStyleSheet(
            """
            QWidget {
                background: transparent;
            }
        """
        )

        # Central widget with rounded corners
        self.central_widget = QWidget()
        self.central_widget.setStyleSheet(
            f"""
            QWidget {{
                background: {self.color.name()};
                border-radius: 12px;
                border: 1px solid rgba(0, 0, 0, 0.1);
            }}
        """
        )

        # Layout
        layout = QVBoxLayout(self.main_widget)
        # layout.setContentsMargins(8, 8, 8, 8)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.central_widget)

        # Central widget layout
        central_layout = QVBoxLayout(self.central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)

        # Title bar
        self.title_bar = self.create_title_bar()
        central_layout.addWidget(self.title_bar)

        # Text editor
        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(
            """
            QTextEdit {
                background: transparent;
                border: none;
                padding: 10px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
                font-size: 14px;
                line-height: 1.5;
                color: #333;
            }
            QTextEdit:focus {
                outline: none;
            }
        """
        )
        self.text_edit.textChanged.connect(self.on_text_changed)

        # 通过设置窗口标志支持大小调整

        # Markdown preview
        self.preview_browser = QTextBrowser()
        self.preview_browser.setStyleSheet(
            """
            QTextBrowser {
                background: transparent;
                border: none;
                padding: 10px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
                font-size: 14px;
                line-height: 1.5;
                color: #333;
            }
        """
        )
        self.preview_browser.setOpenExternalLinks(True)

        # Stack widget to switch between edit and preview
        self.content_stack = QWidget()
        content_layout = QVBoxLayout(self.content_stack)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.text_edit)
        content_layout.addWidget(self.preview_browser)

        # Initially show text edit
        self.text_edit.show()
        self.preview_browser.hide()

        central_layout.addWidget(self.content_stack)

        self.setCentralWidget(self.main_widget)
        self.resize(self.expand_windown_size[0], self.expand_windown_size[1])

    def create_title_bar(self):
        title_bar = QWidget()
        title_bar.setStyleSheet(
            """
            QWidget {
                background: rgba(0, 0, 0, 0.05);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                padding: 5px;
            }
        """
        )

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(5, 5, 5, 5)

        # Toggle preview button
        self.preview_btn = QPushButton("📄")
        self.preview_btn.setFixedSize(24, 24)
        self.preview_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.1);
            }
            QPushButton:checked {
                background: rgba(0, 120, 255, 0.2);
            }
        """
        )
        self.preview_btn.setCheckable(True)
        self.preview_btn.toggled.connect(self.toggle_preview)
        layout.addWidget(self.preview_btn)

        # Color picker button
        color_btn = QPushButton("🎨")
        color_btn.setFixedSize(24, 24)
        color_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.1);
            }
        """
        )
        color_btn.clicked.connect(self.choose_color)
        layout.addWidget(color_btn)

        # Opacity slider
        layout.addWidget(QLabel("💡"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(85)
        self.opacity_slider.setFixedWidth(80)
        self.opacity_slider.valueChanged.connect(self.set_opacity)
        layout.addWidget(self.opacity_slider)

        layout.addStretch()

        # Close button
        close_btn = QPushButton("❌")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 0, 0, 0.2);
                color: red;
            }
        """
        )
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        return title_bar

    def setup_edge_detection(self):
        self.edge_timer = QTimer()
        self.edge_timer.timeout.connect(self.check_edge_position)
        self.edge_timer.start(100)

        self.expand_timer = QTimer()
        self.expand_timer.timeout.connect(self.check_mouse_hover)
        self.expand_timer.start(50)

    def setup_shortcuts(self):
        # Ctrl+N for new note
        new_action = QAction(self)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        new_action.triggered.connect(lambda: self.manager.create_new_note())
        self.addAction(new_action)

        # Ctrl+W for close note
        close_action = QAction(self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)
        self.addAction(close_action)

        # Ctrl+D for duplicate note
        duplicate_action = QAction(self)
        duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        duplicate_action.triggered.connect(lambda: self.manager.duplicate_note(self))
        self.addAction(duplicate_action)

    def setup_context_menu(self):
        """设置右键菜单"""
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, position):
        """显示右键菜单"""
        menu = QMenu(self)

        new_action = QAction("新建便签", menu)
        new_action.triggered.connect(self.manager.create_new_note)
        menu.addAction(new_action)

        duplicate_action = QAction("复制便签", menu)
        duplicate_action.triggered.connect(lambda: self.manager.duplicate_note(self))
        menu.addAction(duplicate_action)

        delete_action = QAction("删除便签", menu)
        delete_action.triggered.connect(self.close)
        menu.addAction(delete_action)

        menu.exec_(self.mapToGlobal(position))

    def check_edge_position(self):
        if self.dragging:
            return

        screen = QApplication.screenAt(self.pos())
        if not screen:
            return

        screen_geo = screen.geometry()
        margin = 50  # Increased from 20px to 50px for easier docking

        x, y = self.x(), self.y()
        width, height = self.width(), self.height()

        new_pos = None
        new_edge = None

        # Check left edge
        if x - screen_geo.left() <= margin:
            new_pos = QPoint(screen_geo.left(), y)
            new_edge = "left"
        # Check right edge
        elif screen_geo.right() - (x + width) <= margin:
            new_pos = QPoint(screen_geo.right() - width, y)
            new_edge = "right"
        # Check top edge
        elif y - screen_geo.top() <= margin:
            new_pos = QPoint(x, screen_geo.top())
            new_edge = "top"
        # Check bottom edge
        elif screen_geo.bottom() - (y + height) <= margin:
            new_pos = QPoint(x, screen_geo.bottom() - height)
            new_edge = "bottom"

        if new_pos and new_pos != self.pos():
            self.move(new_pos)
            self.edge_snapped = new_edge

    def check_mouse_hover(self):
        if not self.isVisible():
            return

        cursor_pos = QCursor.pos()

        # Add buffer zone to prevent jitter
        if self.edge_snapped and self.is_expanded:
            # When expanded, check if mouse is actually outside with buffer
            buffer = 20  # pixels buffer to prevent jitter
            buffered_geo = self.geometry().adjusted(-buffer, -buffer, buffer, buffer)
            if not buffered_geo.contains(cursor_pos):
                self.title_bar.hide()
                self.collapse()
        elif self.edge_snapped and not self.is_expanded:
            # When collapsed, check if mouse is in trigger zone
            trigger_zone = self.get_trigger_zone()
            if trigger_zone.contains(cursor_pos):
                self.title_bar.show()
                self.expand()

    def get_trigger_zone(self):
        """Get the trigger zone for collapsed notes to prevent jitter"""
        if not self.edge_snapped:
            return self.geometry()

        screen = QApplication.screenAt(self.pos())
        if not screen:
            return self.geometry()

        screen_geo = screen.geometry()
        current_geo = self.geometry()

        # Create larger trigger zone when collapsed
        trigger_width = 50  # Wider trigger zone
        trigger_height = 50  # Taller trigger zone

        if self.edge_snapped == "left":
            return QRect(
                screen_geo.left(), current_geo.y(), trigger_width, current_geo.height()
            )
        elif self.edge_snapped == "right":
            return QRect(
                screen_geo.right() - trigger_width,
                current_geo.y(),
                trigger_width,
                current_geo.height(),
            )
        elif self.edge_snapped == "top":
            return QRect(
                current_geo.x(), screen_geo.top(), current_geo.width(), trigger_height
            )
        elif self.edge_snapped == "bottom":
            return QRect(
                current_geo.x(),
                screen_geo.bottom() - trigger_height,
                current_geo.width(),
                trigger_height,
            )

        return self.geometry()

    def expand(self):
        if not self.edge_snapped:
            return

        screen = QApplication.screenAt(self.pos())
        if not screen:
            return

        screen_geo = screen.geometry()
        current_geo = self.geometry()

        self.is_expanded = True
        self.text_edit.setStyleSheet(
            """
            QTextEdit {
                background: transparent;
                border: none;
                padding: 10px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
                font-size: 14px;
                line-height: 1.5;
                color: #333;
            }
            QTextEdit:focus {
                outline: none;
            }
        """
        )
        new_width = self.expand_windown_size[0]
        new_height = self.expand_windown_size[1]
        self.set_content(self.full_content)
        if self.edge_snapped == "left":
            new_geo = QRect(screen_geo.left(), current_geo.y(), new_width, new_height)
        elif self.edge_snapped == "right":
            new_geo = QRect(
                screen_geo.right() - new_width,
                current_geo.y(),
                new_width,
                new_height,
            )
        elif self.edge_snapped == "top":
            new_geo = QRect(current_geo.x(), screen_geo.top(), new_width, new_height)
        elif self.edge_snapped == "bottom":
            new_geo = QRect(
                current_geo.x(),
                screen_geo.bottom() - new_height,
                new_width,
                new_height,
            )
        else:
            return

        self.setGeometry(new_geo)

        # 还原正常显示
        # self.showNormal()
        self.toggle_preview(self.show_preview, change_default=False)
        self.manager.save_notes()

    def collapse(self):
        if not self.edge_snapped:
            return

        screen = QApplication.screenAt(self.pos())
        if not screen:
            return

        self.is_expanded = False
        self.text_edit.setStyleSheet(
            """
            QTextEdit {
                background: transparent;
                border: none;
                padding: 0px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
                font-size: 14px;
                line-height: 1.5;
                color: #333;
            }
            QTextEdit:focus {
                outline: none;
            }
        """
        )

        screen_geo = screen.geometry()
        current_geo = self.geometry()

        # 还原最初样式：缩小后就是小长条，不显示文字
        def mini_content(content):
            mini_content = content.split("\n")[0]
            while True:
                for char in ("#", "-", "*", " ", "\n", "\t"):
                    if mini_content.startswith(char):
                        mini_content = mini_content.strip(char)
                        break
                else:
                    break
            width = 0
            for char in mini_content:
                if ord(char) > 127:
                    width += 17
                else:
                    width += 9
            return mini_content, width

        new_content, new_width = mini_content(self.full_content)
        self.set_content(new_content)
        new_height = self.collapse_windown_size[1]
        new_width = max(new_width, self.collapse_windown_size[0])
        if self.edge_snapped == "left":
            new_geo = QRect(screen_geo.left(), current_geo.y(), new_width, new_height)
        elif self.edge_snapped == "right":
            new_geo = QRect(
                screen_geo.right() - new_width, current_geo.y(), new_width, new_height
            )
        elif self.edge_snapped == "top":
            new_geo = QRect(
                current_geo.x(), screen_geo.top(), current_geo.width(), new_height
            )
        elif self.edge_snapped == "bottom":
            new_geo = QRect(
                current_geo.x(),
                screen_geo.bottom() - new_height,
                current_geo.width(),
                new_height,
            )
        else:
            return

        self.setGeometry(new_geo)
        self.toggle_preview(False, change_default=False)
        # self.manager.save_notes()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            if event.y() < 30:  # 标题栏区域拖拽
                self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                self.dragging = True
                self.edge_snapped = None
                self.is_expanded = True
                event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self.drag_pos:
            self.move(event.globalPos() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.drag_pos = None
        self.dragging = False

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self)
        if color.isValid():
            self.set_color(color)

    def set_color(self, color):
        self.color = color
        self.central_widget.setStyleSheet(
            f"""
            QWidget {{
                background: {color.name()};
                border-radius: 12px;
                border: 1px solid rgba(0, 0, 0, 0.1);
            }}
        """
        )

    def set_always_on_top(self, enabled):
        """设置窗口置顶状态"""
        if enabled:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show()

    def showEvent(self, event):
        """显示时确保置顶并在所有桌面显示"""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

        # macOS: 设置窗口级别以在所有桌面显示
        if MACOS_AVAILABLE:
            try:
                win_id = int(self.winId())
                ns_view = objc.objc_object(c_void_p=win_id)
                ns_window = ns_view.window()
                # 使用 NSFloatingWindowLevel 确保在所有空间显示
                ns_window.setLevel_(NSFloatingWindowLevel + 1)
                # 确保窗口在所有空间都可见
                behavior = ns_window.collectionBehavior()
                behavior |= NSWindowCollectionBehaviorCanJoinAllSpaces
                ns_window.setCollectionBehavior_(behavior)
            except Exception as e:
                print(f"macOS window level setup failed: {e}")
                pass

    def set_opacity(self, value):
        self.setWindowOpacity(value / 100)

    def set_content(self, content, replace=False):
        if replace:
            self.full_content = content
        self.text_edit.setPlainText(content)
        self.update_preview(content)

    def get_content(self):
        # return self.text_edit.toPlainText()
        return self.full_content

    def on_text_changed(self):
        if not self.is_expanded:
            return
        self.full_content = self.text_edit.toPlainText()
        if self.manager._loading:
            return  # 防止加载时的循环
        self.update_preview()
        self.manager.save_notes()

        print(f"Auto-saved note: {self.note_id}")  # Debug output

    def toggle_preview(self, checked, change_default=True):
        if checked:
            if change_default:
                self.show_preview = True
            self.text_edit.hide()
            self.preview_browser.show()
            self.preview_btn.setText("✏️")
        else:
            if change_default:
                self.show_preview = False
            self.text_edit.show()
            self.preview_browser.hide()
            self.preview_btn.setText("📄")

    def update_preview(self, content=None):
        """Update the markdown preview"""
        if content is None:
            content = self.text_edit.toPlainText()
        if content.strip():
            try:
                rendered_html = markdown.markdown(
                    content, extensions=["fenced_code", "tables", "nl2br"]
                )
                self.preview_browser.setHtml(
                    f"""
                    <style>
                        body {{ 
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto; 
                            font-size: 14px; 
                            line-height: 1.5; 
                            color: #333; 
                            margin: 0; 
                            padding: 0; 
                        }}
                        h1, h2, h3, h4, h5, h6 {{ margin: 10px 0; }}
                        p {{ margin: 8px 0; }}
                        ul, ol {{ margin: 8px 0; padding-left: 25px; }}
                        code {{ 
                            background: rgba(0,0,0,0.1); 
                            padding: 2px 4px; 
                            border-radius: 3px; 
                            font-family: 'Courier New', monospace;
                        }}
                        pre {{ 
                            background: rgba(0,0,0,0.1); 
                            padding: 10px; 
                            border-radius: 5px; 
                            overflow-x: auto;
                        }}
                        blockquote {{ 
                            border-left: 3px solid #ccc; 
                            margin: 8px 0; 
                            padding-left: 15px; 
                            color: #666; 
                        }}
                        table {{ border-collapse: collapse; width: 100%; }}
                        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                        th {{ background: rgba(0,0,0,0.05); }}
                    </style>
                    {rendered_html}
                """
                )
            except RecursionError:
                # Handle potential recursion in markdown parsing
                self.preview_browser.setHtml(
                    "<div style='color: red; padding: 10px;'>Error rendering markdown: Content too complex</div>"
                )
            except Exception as e:
                self.preview_browser.setHtml(
                    f"<div style='color: red; padding: 10px;'>Error: {str(e)}</div>"
                )
        else:
            self.preview_browser.setHtml(
                """
                <div style="color: #999; font-style: italic; text-align: center; margin-top: 20px;">
                    Start typing to see markdown preview...
                </div>
            """
            )

    def moveEvent(self, event):
        super().moveEvent(event)
        self.manager.save_notes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.manager.save_notes()

    def closeEvent(self, event):
        self.manager.close_note(self.note_id, save=False)
        event.accept()


class SystemTrayIcon(QSystemTrayIcon):
    def create_icon(self):
        """创建托盘图标"""
        # 创建简单的图标
        icon = QIcon()
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor("#FFF9C4"))

        # 绘制简单的便签图标
        from PyQt5.QtGui import QPainter, QPen, QFont

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # 背景
        painter.setBrush(QColor("#FFF9C4"))
        painter.setPen(QPen(QColor("#333"), 1))
        painter.drawRect(2, 2, 28, 28)

        # 文本行
        painter.setPen(QPen(QColor("#333"), 1))
        for i in range(3):
            painter.drawLine(6, 8 + i * 6, 26, 8 + i * 6)

        painter.end()
        icon.addPixmap(pixmap)
        return icon

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.always_on_top = True

        self.setIcon(self.create_icon())
        self.setToolTip("PyStickies")

        # Create menu
        menu = QMenu()

        new_action = QAction("新建便签", menu)
        new_action.triggered.connect(self.manager.create_new_note)
        menu.addAction(new_action)

        show_action = QAction("显示全部", menu)
        show_action.triggered.connect(self.manager.show_all_notes)
        menu.addAction(show_action)

        hide_action = QAction("隐藏全部", menu)
        hide_action.triggered.connect(self.manager.hide_all_notes)
        menu.addAction(hide_action)

        topmost_action = QAction("保持置顶", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(True)
        topmost_action.triggered.connect(self.toggle_always_on_top)
        menu.addAction(topmost_action)

        delete_action = QAction("删除全部", menu)
        delete_action.triggered.connect(self.manager.delete_all_notes)
        menu.addAction(delete_action)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self.on_tray_activated)

        # 确保托盘图标可见
        self.setVisible(True)

        # 显示提示，告诉用户如何使用
        self.showMessage(
            "PyStickies", "右键点击托盘图标新建便签", QSystemTrayIcon.Information, 3000
        )

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.manager.show_all_notes()

    def toggle_always_on_top(self, checked):
        """切换置顶状态"""
        self.always_on_top = checked
        for note in self.manager.notes.values():
            note.set_always_on_top(checked)


def main():
    app = QApplication(sys.argv)

    # Set app style for dark mode support
    app.setStyle("Fusion")

    manager = StickyNoteManager()

    # Create system tray
    tray = SystemTrayIcon(manager)
    tray.show()

    # Create initial note if no saved notes
    if not manager.notes:
        print("No saved notes found, creating new note")
        manager.create_new_note()

    # 确保程序退出时保存数据
    # app.aboutToQuit.connect(manager.cleanup_and_exit)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
