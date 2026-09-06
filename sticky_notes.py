import os, sys, json, traceback, signal, shutil, fcntl, urllib.request, urllib.error
from datetime import datetime
from ctypes import c_void_p
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
    QMessageBox,
    QSizeGrip,
    QGraphicsDropShadowEffect,
)
from PyQt5.QtCore import (
    Qt,
    QTimer,
    QPropertyAnimation,
    QRect,
    QPoint,
    QSettings,
    QEvent,
    QEasingCurve,
    pyqtSignal,
    QObject,
    QThread,
    pyqtSlot,
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
    QPainter,
    QPen,
)

# 全局快捷键支持
try:
    from pynput import keyboard
    GLOBAL_HOTKEYS_AVAILABLE = True
except ImportError:
    GLOBAL_HOTKEYS_AVAILABLE = False

# macOS specific imports for all-desktop support
try:
    import objc
    from AppKit import NSStatusWindowLevel, NSFloatingWindowLevel, NSApplicationActivationPolicyAccessory
    from Cocoa import NSApplication, NSApp, NSWindowCollectionBehaviorCanJoinAllSpaces

    MACOS_AVAILABLE = True
except ImportError:
    MACOS_AVAILABLE = False


# 便签预设色卡（最后一个为深色，用于暗色适配）
PRESET_COLORS = ["#FFF9C4", "#FFD1DC", "#C8E6C9", "#BBDEFB", "#E1BEE7", "#FFE0B2", "#4A4A4A"]

# 窗口四周的恒定边距（阴影空间）。吸附时窗口探出屏幕外 CHROME_MARGIN 像素，
# 让卡片始终贴合屏幕边缘——吸附/拖出时卡片尺寸因此完全一致，不会突变
CHROME_MARGIN = 12


def make_icon(kind, color=None, size=16):
    """自绘矢量图标：风格统一，替代 emoji（不同系统渲染不一致）"""
    if color is None:
        color = QColor("#444")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    if kind == "preview":  # 眼睛：进入预览
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(1, 4, size - 2, size - 8)
        painter.setBrush(color)
        painter.drawEllipse(size // 2 - 2, size // 2 - 2, 4, 4)
    elif kind == "edit":  # 铅笔：返回编辑
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(3, size - 3, size - 7, 6)
        painter.drawLine(size - 7, 6, size - 3, 2)
    elif kind == "palette":  # 调色盘：换颜色
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.setBrush(color)
        painter.drawEllipse(5, 5, 3, 3)
        painter.drawEllipse(size - 8, 5, 3, 3)
        painter.drawEllipse(size // 2 - 1, size - 8, 3, 3)
    elif kind == "opacity":  # 半满圆：透明度
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.setBrush(color)
        painter.drawPie(2, 2, size - 4, size - 4, -90 * 16, 180 * 16)
    elif kind == "close":  # 叉：关闭
        painter.drawLine(4, 4, size - 4, size - 4)
        painter.drawLine(size - 4, 4, 4, size - 4)

    painter.end()
    return QIcon(pixmap)


class GistWorker(QThread):
    """Gist 同步线程：请求排队后串行处理，避免并发写冲突"""

    save_completed = pyqtSignal(bool, str)
    load_completed = pyqtSignal(bool, str, dict)

    def __init__(self, github_token=None, gist_id=None):
        super().__init__()
        self.github_token = github_token
        self.gist_id = gist_id
        self.filename = "pystickies_notes.json"
        self.base_url = "https://api.github.com"
        # 由 manager 在发起加载前设置，用于跳过无变化的远端
        self.last_known_updated_at = None
        # 最近一次成功请求后远端返回的 updated_at
        self.updated_at = None
        self._pending_save = None
        self._pending_load = False

    def save_to_gist(self, data):
        # 线程运行中只记录最新数据，由 run 循环接着处理
        self._pending_save = data
        if not self.isRunning():
            self.start()

    def load_from_gist(self):
        self._pending_load = True
        if not self.isRunning():
            self.start()

    def run(self):
        if not self.github_token:
            if self._pending_save is not None:
                self._pending_save = None
                self.save_completed.emit(False, "GitHub token not configured")
            if self._pending_load:
                self._pending_load = False
                self.load_completed.emit(False, "GitHub token not configured", {})
            return

        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "PyStickies-App",
        }

        # 串行处理：运行期间新到的请求在循环里继续消化
        while True:
            if self._pending_load:
                self._pending_load = False
                try:
                    self._load_gist(headers)
                except Exception as e:
                    self.load_completed.emit(False, str(e), {})
            elif self._pending_save is not None:
                data = self._pending_save
                self._pending_save = None
                try:
                    self._save_gist(headers, data)
                except Exception as e:
                    self.save_completed.emit(False, str(e))
            else:
                break

    def _request(self, method, url, headers, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _save_gist(self, headers, data):
        gist_data = {
            "description": "PyStickies - Auto-synced notes",
            "files": {
                self.filename: {
                    "content": json.dumps(data, ensure_ascii=False, indent=2)
                }
            },
            "public": False,
        }

        if self.gist_id:
            # Update existing gist
            url = f"{self.base_url}/gists/{self.gist_id}"
            status, body = self._request("PATCH", url, headers, gist_data)
            if status == 200:
                self.updated_at = json.loads(body).get("updated_at")
                self.save_completed.emit(True, "Notes synced to Gist")
            else:
                self.save_completed.emit(False, f"Failed to update gist: {status} - {body.decode('utf-8', 'replace')}")
        else:
            # Create new gist
            url = f"{self.base_url}/gists"
            status, body = self._request("POST", url, headers, gist_data)
            if status == 201:
                result = json.loads(body)
                self.gist_id = result["id"]
                self.updated_at = result.get("updated_at")
                self.save_completed.emit(True, f"Created new gist: {self.gist_id}")
            else:
                self.save_completed.emit(False, f"Failed to create gist: {status} - {body.decode('utf-8', 'replace')}")

    def _load_gist(self, headers):
        if not self.gist_id:
            self.load_completed.emit(False, "No gist ID configured", {})
            return

        url = f"{self.base_url}/gists/{self.gist_id}"
        status, body = self._request("GET", url, headers)
        if status == 200:
            result = json.loads(body)
            remote_updated_at = result.get("updated_at")
            # 远端无变化时跳过，避免覆盖本地未推送的编辑
            if remote_updated_at == self.last_known_updated_at:
                self.load_completed.emit(True, "Gist 无更新，跳过加载", {})
                return
            if self.filename in result["files"]:
                content = result["files"][self.filename]["content"]
                try:
                    notes_data = json.loads(content)
                    self.updated_at = remote_updated_at
                    self.load_completed.emit(True, "Notes loaded from Gist", notes_data)
                except json.JSONDecodeError as e:
                    self.load_completed.emit(False, f"Invalid JSON in gist: {e}", {})
            else:
                self.load_completed.emit(False, f"File {self.filename} not found in gist", {})
        elif status == 404:
            self.load_completed.emit(False, f"Gist {self.gist_id} not found", {})
        else:
            self.load_completed.emit(False, f"Failed to load gist: {status} - {body.decode('utf-8', 'replace')}", {})


class StickyNoteManager(QObject):
    note_closed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.notes = {}
        self._hidden_states = {}  # 热键隐藏前各便签的位置和吸附状态
        self._loading = False  # 防止加载时的循环
        self.settings_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "notes_data.json"
        )
        self.gist_config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".gist_config"
        )
        
        # Load Gist configuration
        self.github_token = None
        self.gist_id = None
        self.gist_updated_at = None
        self.load_gist_config()

        # Initialize Gist worker
        self.gist_worker = GistWorker(self.github_token, self.gist_id)
        self.gist_worker.save_completed.connect(self.on_gist_save_completed)
        self.gist_worker.load_completed.connect(self.on_gist_load_completed)

        # 本地保存防抖（500ms）：避免拖动/输入时频繁写文件
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self._save_notes_now)

        # Gist 同步防抖（5s）：停止编辑后再推送，避免触发 API 限流
        self.gist_sync_timer = QTimer(self)
        self.gist_sync_timer.setSingleShot(True)
        self.gist_sync_timer.setInterval(5000)
        self.gist_sync_timer.timeout.connect(self._sync_to_gist_now)

        # 统一轮询所有便签的边缘吸附与悬停展开（替代每个便签各自的定时器）
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(60)
        self.poll_timer.timeout.connect(self._poll_notes)
        self.poll_timer.start()

        # 初始化全局快捷键
        self.global_hotkey_manager = GlobalHotkeyManager(self)
        
        # 不再自动同步，改为手动同步
        # self.sync_timer = QTimer()
        # self.sync_timer.timeout.connect(self.sync_from_gist)
        # self.sync_timer.start(60000)  # 注释掉自动同步
        
        self.load_notes()

    def load_gist_config(self):
        """Load Gist configuration from file or environment"""
        # Try environment variables first
        self.github_token = os.getenv('PYSTICKIES_GITHUB_TOKEN')
        self.gist_id = os.getenv('PYSTICKIES_GIST_ID')

        # Try config file
        if os.path.exists(self.gist_config_file):
            try:
                with open(self.gist_config_file, 'r') as f:
                    config = json.load(f)
                    self.github_token = config.get('github_token', self.github_token)
                    self.gist_id = config.get('gist_id', self.gist_id)
                    self.gist_updated_at = config.get('gist_updated_at', self.gist_updated_at)
            except Exception as e:
                print(f"Failed to load Gist config: {e}")

        # Update Gist worker with new credentials
        if hasattr(self, 'gist_worker'):
            self.gist_worker.github_token = self.github_token
            self.gist_worker.gist_id = self.gist_id

    def save_gist_config(self):
        """Save Gist configuration to file"""
        config = {
            'github_token': self.github_token,
            'gist_id': self.gist_id,
            'gist_updated_at': self.gist_updated_at
        }
        try:
            with open(self.gist_config_file, 'w') as f:
                json.dump(config, f, indent=2)
            # Update Gist worker
            if hasattr(self, 'gist_worker'):
                self.gist_worker.github_token = self.github_token
                self.gist_worker.gist_id = self.gist_id
        except Exception as e:
            print(f"Failed to save Gist config: {e}")

    def sync_from_gist(self):
        """从 Gist 加载便签内容（远端无变化时自动跳过）"""
        if not self.github_token or not self.gist_id:
            print("Skipping Gist sync - GitHub token or Gist ID not configured")
            return

        print("Starting sync from Gist...")
        self.gist_worker.last_known_updated_at = self.gist_updated_at
        self.gist_worker.load_from_gist()
    
    def load_notes(self):
        """Always load layout from local file, sync content from Gist separately"""
        notes_data = {}
        
        # 总是先加载本地文件（包含布局信息）
        local_file_exists = os.path.exists(self.settings_file)
        if local_file_exists:
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    notes_data = json.load(f)
                print(f"Loaded {len(notes_data)} notes from local file")
            except Exception as e:
                print(f"加载本地便签失败: {e}")
        
        # 加载本地数据（包含完整布局信息）
        self._load_notes_from_data(notes_data)
        
        # 异步从Gist同步内容（不影响布局）
        if self.github_token and self.gist_id:
            print("Starting background content sync from Gist...")
            self.gist_worker.last_known_updated_at = self.gist_updated_at
            self.gist_worker.load_from_gist()
    
    def _validate_screen_position(self, position, size, screen_index):
        """保持原始位置和尺寸，不做调整"""
        return position, screen_index, size

    def _load_notes_from_data(self, notes_data):
        """Internal method to load notes from data dict - 支持新旧格式和Gist数据"""
        if not notes_data:
            return
            
        # 完全清除现有便签，重新加载
        existing_notes = list(self.notes.keys())
        for note_id in existing_notes:
            if note_id in self.notes:
                self.notes[note_id].close_without_confirmation()
                if note_id in self.notes:
                    del self.notes[note_id]
        
        # 处理新旧数据格式和Gist数据格式
        for note_id, note_data in notes_data.items():
            try:
                # 检查是新格式（有stable_data和layout_data）还是旧格式或Gist格式
                if "stable_data" in note_data and "layout_data" in note_data:
                    # 新格式（完整格式）
                    stable_data = note_data["stable_data"]
                    layout_data = note_data["layout_data"]
                    
                    content = stable_data.get("content", "")
                    color = QColor(stable_data.get("color", "#FFF9C4"))
                    opacity = stable_data.get("opacity", 85)
                    locked = stable_data.get("locked", False)

                    position = QPoint(layout_data["position"][0], layout_data["position"][1])
                    size = layout_data["size"]
                    screen_index = layout_data.get("screen_index", 0)
                    is_expanded = layout_data.get("is_expanded", False)
                    edge_snapped = layout_data.get("edge_snapped", None)
                    dock_mode = layout_data.get("dock_mode", "strip")

                elif "stable_data" in note_data:
                    # Gist格式（只有stable_data）或简化的稳定数据
                    stable_data = note_data["stable_data"]
                    
                    content = stable_data.get("content", "")
                    color = QColor(stable_data.get("color", "#FFF9C4"))
                    opacity = stable_data.get("opacity", 85)
                    locked = stable_data.get("locked", False)

                    # 为缺失的布局数据提供默认值
                    screens = QApplication.screens()
                    current_screen = QApplication.screenAt(QCursor.pos())
                    screen_index = screens.index(current_screen) if current_screen else 0
                    screen_geo = screens[screen_index].geometry()
                    
                    position = QPoint(screen_geo.x() + 100, screen_geo.y() + 100)
                    size = [300, 200]  # 默认大小
                    is_expanded = True
                    edge_snapped = None
                    dock_mode = "strip"

                elif "content" in note_data and "position" in note_data:
                    # 旧格式
                    content = note_data.get("content", "")
                    color = QColor(note_data.get("color", "#FFF9C4"))
                    opacity = note_data.get("opacity", 85)
                    locked = False

                    position = QPoint(note_data["position"][0], note_data["position"][1])
                    size = note_data["size"]
                    screen_index = note_data.get("screen_index", 0)
                    is_expanded = note_data.get("is_expanded", False)
                    edge_snapped = note_data.get("edge_snapped", None)
                    dock_mode = "strip"
                
                else:
                    # 最简格式（只有内容）
                    content = str(note_data) if isinstance(note_data, str) else ""
                    color = QColor("#FFF9C4")
                    opacity = 85
                    locked = False
                    
                    # 提供默认布局数据
                    screens = QApplication.screens()
                    current_screen = QApplication.screenAt(QCursor.pos())
                    screen_index = screens.index(current_screen) if current_screen else 0
                    screen_geo = screens[screen_index].geometry()
                    
                    position = QPoint(screen_geo.x() + 100, screen_geo.y() + 100)
                    size = [300, 200]
                    is_expanded = True
                    edge_snapped = None
                    dock_mode = "strip"

                # 创建新便签，但不触发任何事件
                note = self.display_note(
                    note_id=note_id,
                    content=content,
                    position=position,
                    size=size,
                    color=color,
                    opacity=opacity,
                    screen_index=screen_index,
                )
                note.is_expanded = is_expanded
                note.edge_snapped = edge_snapped
                note.dock_mode = dock_mode
                # 卷帘折叠是临时状态，加载时一律展开
                if note.edge_snapped is None and not note.is_expanded:
                    note.is_expanded = True
                note.toggle_lock(locked)
                # 吸附折叠状态：重放 collapse 重建细条/色块外观
                # （加载只恢复了几何和标记，外观需要这里建立）
                if note.edge_snapped and not note.is_expanded:
                    note.title_bar.hide()
                    note.collapse()

            except Exception as e:
                print(f"Failed to load note {note_id}: {e}")
                # 如果解析失败，创建带有默认值的便签
                try:
                    screens = QApplication.screens()
                    current_screen = QApplication.screenAt(QCursor.pos())
                    screen_index = screens.index(current_screen) if current_screen else 0
                    screen_geo = screens[screen_index].geometry()
                    
                    note = self.display_note(
                        note_id=note_id,
                        content=str(note_data)[:100] + "..." if len(str(note_data)) > 100 else str(note_data),
                        position=QPoint(screen_geo.x() + 100, screen_geo.y() + 100),
                        size=[300, 200],
                        color=QColor("#FFF9C4"),
                        opacity=85,
                        screen_index=screen_index,
                    )
                    self.notes[note_id] = note
                except Exception as e2:
                    print(f"Critical error creating fallback note: {e2}")
        
        # 只在完全没有便签时创建初始便签
        if not self.notes and not notes_data:
            print("No notes found, creating initial note")
            self.create_new_note()

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
            # 带微秒避免同一秒内新建/复制产生相同 id 互相覆盖
            note_id = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
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
                # 通过滑块设置，保证界面滑块位置与实际透明度一致
                note.opacity_slider.setValue(opacity)
        finally:
            self._loading = False

        note.screen_index = screen_index
        self.notes[note_id] = note
        note.show()
        return note

    def save_notes(self, sync_gist=True):
        """请求保存：本地写盘防抖 500ms，Gist 推送防抖 5s（避免频繁 IO 和 API 限流）"""
        self.save_timer.start()  # singleShot 定时器重启即实现防抖
        if sync_gist and self.github_token:
            self.gist_sync_timer.start()

    def sync_to_gist(self):
        """手动触发：立即落盘并推送 Gist"""
        self.save_timer.stop()
        self._save_notes_now()
        self.gist_sync_timer.stop()
        self._sync_to_gist_now()

    def _save_notes_now(self):
        """实际写入本地文件（由防抖定时器触发，退出前也会直接调用）"""
        self.save_timer.stop()
        notes_data = {}

        try:
            for note_id, note in self.notes.items():
                # 保存所有便签，不管是否可见
                try:
                    screen = QApplication.screenAt(note.geometry().center())
                    screen_index = (
                        QApplication.screens().index(screen) if screen else 0
                    )
                    screen_geo = QApplication.screens()[screen_index].geometry()

                    # 计算相对屏幕坐标
                    rel_x = note.x() - screen_geo.x()
                    rel_y = note.y() - screen_geo.y()

                    notes_data[note_id] = {
                        "stable_data": {
                            "content": str(note.get_content() or ""),
                            "color": str(note.color.name() or "#FFF9C4"),
                            "opacity": int(note.opacity_slider.value() or 85),
                            "locked": bool(note.is_locked),
                        },
                        "layout_data": {
                            "position": [int(rel_x), int(rel_y)],
                            "size": [int(note.width()), int(note.height())],
                            "screen_index": int(screen_index),
                            "is_expanded": bool(note.is_expanded),
                            "edge_snapped": (
                                str(note.edge_snapped)
                                if note.edge_snapped is not None
                                else None
                            ),
                            "dock_mode": str(note.dock_mode),
                        }
                    }
                except Exception as e:
                    print(f"保存便签时出错: {e}")

            # 原子写入：先写临时文件再替换，避免写一半崩溃损坏数据；同时轮换一份 .bak
            tmp_file = self.settings_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(notes_data, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.settings_file):
                shutil.copy2(self.settings_file, self.settings_file + ".bak")
            os.replace(tmp_file, self.settings_file)

        except Exception as e:
            print(f"Critical file save error: {e}")
            traceback.print_exc()

    def _sync_to_gist_now(self):
        """实际推送 Gist：只包含稳定数据（内容、颜色、透明度），不含布局"""
        self.gist_sync_timer.stop()
        if not self.github_token:
            return
        gist_data = {}
        for note_id, note in self.notes.items():
            gist_data[note_id] = {
                "stable_data": {
                    "content": str(note.get_content() or ""),
                    "color": str(note.color.name() or "#FFF9C4"),
                    "opacity": int(note.opacity_slider.value() or 85),
                    "locked": bool(note.is_locked),
                }
            }
        self.gist_worker.save_to_gist(gist_data)

    @pyqtSlot(bool, str)
    def on_gist_save_completed(self, success, message):
        """Handle Gist save completion"""
        if success:
            # 新建 gist 或远端版本前进后，持久化 gist_id 和 updated_at
            changed = False
            if self.gist_worker.gist_id and self.gist_worker.gist_id != self.gist_id:
                self.gist_id = self.gist_worker.gist_id
                changed = True
            if self.gist_worker.updated_at and self.gist_worker.updated_at != self.gist_updated_at:
                self.gist_updated_at = self.gist_worker.updated_at
                changed = True
            if changed:
                self.save_gist_config()
                print(f"Gist 地址（可用于找回数据）: https://gist.github.com/{self.gist_id}")
            print(f"Gist sync: {message}")
        else:
            print(f"Gist sync failed: {message}")

    @pyqtSlot(bool, str, dict)
    def on_gist_load_completed(self, success, message, notes_data):
        """Handle Gist load completion - update content only, never touch layout"""
        if success:
            print(f"Gist load: {message}")
            if self.gist_worker.updated_at and self.gist_worker.updated_at != self.gist_updated_at:
                self.gist_updated_at = self.gist_worker.updated_at
                self.save_gist_config()
            # 只更新内容，绝不改变位置或大小
            self._loading = True
            try:
                # 加载本地完整数据（包含布局信息）
                local_data = {}
                if os.path.exists(self.settings_file):
                    try:
                        with open(self.settings_file, "r", encoding="utf-8") as f:
                            local_data = json.load(f)
                    except Exception as e:
                        print(f"Failed to load local data: {e}")
                
                # 处理每个从Gist加载的便签
                for note_id, gist_note_data in notes_data.items():
                    stable_data = gist_note_data.get("stable_data", {})
                    content = stable_data.get("content", "")
                    color = QColor(stable_data.get("color", "#FFF9C4"))
                    opacity = stable_data.get("opacity", 85)
                    locked = stable_data.get("locked", False)

                    if note_id in self.notes:
                        # 更新现有便签的内容，完全不改变位置
                        note = self.notes[note_id]
                        old_auto_save = note.text_edit.blockSignals(True)
                        try:
                            note.set_content(content, replace=True)
                            note.set_color(color)
                            note.opacity_slider.setValue(opacity)
                            note.toggle_lock(locked)
                        finally:
                            note.text_edit.blockSignals(old_auto_save)
                    else:
                        # 新便签，使用本地布局数据（绝对优先）或默认位置
                        position = None
                        size = None
                        screen_index = 0
                        is_expanded = True
                        edge_snapped = None
                        dock_mode = "strip"

                        # 绝对优先使用本地布局数据
                        if note_id in local_data:
                            local_note_data = local_data[note_id]
                            if "layout_data" in local_note_data:
                                layout_data = local_note_data["layout_data"]
                                position = QPoint(layout_data["position"][0], layout_data["position"][1])
                                size = layout_data["size"]
                                screen_index = layout_data.get("screen_index", 0)
                                is_expanded = layout_data.get("is_expanded", True)
                                edge_snapped = layout_data.get("edge_snapped", None)
                                dock_mode = layout_data.get("dock_mode", "strip")
                            elif "position" in local_note_data and "size" in local_note_data:
                                # 旧格式
                                position = QPoint(local_note_data["position"][0], local_note_data["position"][1])
                                size = local_note_data["size"]
                                screen_index = local_note_data.get("screen_index", 0)
                                is_expanded = local_note_data.get("is_expanded", True)
                                edge_snapped = local_note_data.get("edge_snapped", None)
                        
                        # 只有真正的新便签才使用默认位置
                        if position is None:
                            screens = QApplication.screens()
                            current_screen = QApplication.screenAt(QCursor.pos())
                            screen_index = screens.index(current_screen) if current_screen else 0
                            screen_geo = screens[screen_index].geometry()
                            position = QPoint(screen_geo.x() + 100, screen_geo.y() + 100)
                            size = [300, 200]
                        
                        # 创建新便签
                        note = self.display_note(
                            note_id=note_id,
                            content=content,
                            position=position,
                            size=size,
                            color=color,
                            opacity=opacity,
                            screen_index=screen_index,
                        )
                        note.is_expanded = is_expanded
                        note.edge_snapped = edge_snapped
                        note.dock_mode = dock_mode
                        # 卷帘折叠是临时状态，加载时一律展开
                        if note.edge_snapped is None and not note.is_expanded:
                            note.is_expanded = True
                        note.toggle_lock(locked)
                        # 吸附折叠状态：重放 collapse 重建细条/色块外观
                        if note.edge_snapped and not note.is_expanded:
                            note.title_bar.hide()
                            note.collapse()

                # 保存当前完整状态（内容+布局）到本地，不反向推送 Gist
                self.save_notes(sync_gist=False)
                    
            finally:
                self._loading = False
        else:
            print(f"Gist load failed: {message}")
            # 如果Gist加载失败，正常加载本地数据
            if not self.notes:
                self.load_notes()

    def close_note(self, note_id, save=True):
        """直接删除便签（不显示确认对话框）"""
        if note_id in self.notes:
            del self.notes[note_id]
            if save:
                self.save_notes()
            print(f"Deleted note: {note_id}")

    def cleanup_and_exit(self):
        """程序退出前保存所有数据并清理资源"""
        # 清理全局快捷键
        if hasattr(self, 'global_hotkey_manager'):
            self.global_hotkey_manager.cleanup()

        # 停止防抖定时器并立即落盘
        self.save_timer.stop()
        self.gist_sync_timer.stop()
        self._save_notes_now()
        print("Data saved before exit")

    def _poll_notes(self):
        """统一轮询：边缘吸附检测 + 悬停展开/收缩（替代每个便签各自的定时器）"""
        for note in list(self.notes.values()):
            note.check_edge_position()
            note.check_mouse_hover()

    def show_all_notes(self):
        """热键/托盘显示：按隐藏前状态还原——窗口态还原位置、其余还原细条。
        显示操作绝不留下隐藏态色块（隐藏态是临时状态，细条是最低可见形态）"""
        for note in self.notes.values():
            saved = self._hidden_states.get(note.note_id)
            if saved is None:
                # 不在本次隐藏记录里（隐藏期间新建，或应用启动时就是隐藏态色块）
                if note.edge_snapped and not note.is_expanded and note.dock_mode == "sliver":
                    note.restore_strip()
                else:
                    note.show()
                    note.raise_()
                continue
            geo, prev_edge, prev_mode = saved
            if note.edge_snapped is None:
                continue  # 隐藏期间被用户拖出：尊重用户摆放
            if prev_edge is None:
                note.undock(geo)  # 窗口态：还原浮动位置
            else:
                note.restore_strip()  # 吸附态/隐藏态：还原细条
        self._hidden_states = {}

    def hide_all_notes(self):
        """热键/托盘隐藏：所有便签收缩到屏幕边缘成 6px 色块（鼠标划过自动展开）"""
        self._hidden_states = {}
        for note in self.notes.values():
            self._hidden_states[note.note_id] = (
                note.geometry(),
                note.edge_snapped,
                note.dock_mode,
            )
            note.dock_to_edge()

    def delete_all_notes(self):
        """删除所有便签"""
        if not self.notes:
            print("没有便签可以删除")
            return

        # 显示确认对话框
        msg_box = QMessageBox()
        msg_box.setWindowTitle("确认删除所有便签")
        msg_box.setText(f"确定要删除所有 {len(self.notes)} 个便签吗？")
        msg_box.setInformativeText("此操作不可撤销，所有便签内容将被永久删除。")
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setIcon(QMessageBox.Warning)

        result = msg_box.exec_()

        if result == QMessageBox.Yes:
            note_ids = list(self.notes.keys())
            for note_id in note_ids:
                if note_id in self.notes:  # 确保便签仍然存在
                    # 直接关闭，不再逐个弹确认框
                    self.notes[note_id].close_without_confirmation()
            self.notes.clear()
            self.save_notes()
            print("Deleted all notes")
        else:
            print("Deletion of all notes cancelled")

    def export_notes_markdown(self):
        """导出所有便签为一个 Markdown 文件"""
        if not self.notes:
            print("没有便签可以导出")
            return

        filename = f"pystickies_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        parts = [f"# PyStickies 导出（{datetime.now().strftime('%Y-%m-%d %H:%M')}）\n"]
        for note_id, note in self.notes.items():
            content = (note.get_content() or "").strip()
            title = content.split("\n")[0][:50] if content else "(空便签)"
            parts.append(f"\n## {title}\n\n{content}\n\n---\n")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(parts))
            print(f"已导出 {len(self.notes)} 个便签到 {path}")
            QMessageBox.information(None, "导出完成", f"已导出 {len(self.notes)} 个便签到:\n{path}")
        except Exception as e:
            print(f"导出失败: {e}")

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
        # 吸附形态：strip=显示首行的细条（吸附态）/ sliver=6px 色块（隐藏态）
        self.dock_mode = "strip"
        self.drag_pos = None
        self.dragging = False
        self.resizing = False
        self.expand_windown_size = [300, 200]
        self.collapse_windown_size = [50, 30]
        self.full_content = ""
        self.show_preview = False
        self.is_locked = False
        self._grip_resizing = False
        self._drag_unsnap_pending = False

        self.init_ui()
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

        # Layout（恒定边距给阴影留空间；吸附时窗口探出屏幕外，卡片仍贴合边缘）
        layout = QVBoxLayout(self.main_widget)
        layout.setContentsMargins(CHROME_MARGIN, CHROME_MARGIN, CHROME_MARGIN, CHROME_MARGIN)
        layout.addWidget(self.central_widget)

        # Central widget layout
        central_layout = QVBoxLayout(self.central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)

        # Title bar
        self.title_bar = self.create_title_bar()
        central_layout.addWidget(self.title_bar)

        # Text editor
        self.text_edit = QTextEdit()
        self.text_edit.setStyleSheet(self._editor_style())
        self.text_edit.textChanged.connect(self.on_text_changed)

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

        # 底行：右下角调整大小手柄（无边框窗口没有原生拉伸边）
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setToolTip("拖动调整大小")
        self.size_grip.installEventFilter(self)
        bottom_row.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        central_layout.addLayout(bottom_row)

        self.setCentralWidget(self.main_widget)
        self.resize(self.expand_windown_size[0], self.expand_windown_size[1])

        # 自绘柔和阴影（仅浮动状态启用，吸附时关闭并贴合屏幕边缘）
        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(18)
        self.shadow_effect.setOffset(0, 3)
        self.shadow_effect.setColor(QColor(0, 0, 0, 50))
        self.central_widget.setGraphicsEffect(self.shadow_effect)
        self._update_chrome()

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

        btn_style = """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: rgba(0, 0, 0, 0.1);
            }
            QPushButton:checked {
                background: rgba(0, 120, 255, 0.2);
            }
        """

        # Toggle preview button
        self.preview_btn = QPushButton()
        self.preview_btn.setFixedSize(24, 24)
        self.preview_btn.setStyleSheet(btn_style)
        self.preview_btn.setCheckable(True)
        self.preview_btn.toggled.connect(self.toggle_preview)
        layout.addWidget(self.preview_btn)

        # Color picker button
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(24, 24)
        self.color_btn.setStyleSheet(btn_style)
        self.color_btn.setToolTip("更换颜色（预设色卡 / 自定义）")
        self.color_btn.clicked.connect(self.choose_color)
        layout.addWidget(self.color_btn)

        # Opacity slider
        self.opacity_icon = QLabel()
        self.opacity_icon.setToolTip("拖动调整便签透明度")
        layout.addWidget(self.opacity_icon)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(85)
        self.opacity_slider.setFixedWidth(80)
        self.opacity_slider.setToolTip("拖动调整便签透明度")
        self.opacity_slider.valueChanged.connect(self.set_opacity)
        layout.addWidget(self.opacity_slider)

        layout.addStretch()

        # Close button
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(btn_style)
        self.close_btn.setToolTip("删除便签")
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)

        self._refresh_icons()
        return title_bar

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

        # Ctrl+T 插入当前时间戳
        ts_action = QAction(self)
        ts_action.setShortcut(QKeySequence("Ctrl+T"))
        ts_action.triggered.connect(self.insert_timestamp)
        self.addAction(ts_action)

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

        # 锁定后内容只读且不可删除，防止误改误删
        lock_action = QAction("锁定便签", menu)
        lock_action.setCheckable(True)
        lock_action.setChecked(self.is_locked)
        lock_action.triggered.connect(self.toggle_lock)
        menu.addAction(lock_action)

        # 添加手动同步功能
        sync_action = QAction("同步到Gist", menu)
        sync_action.triggered.connect(lambda: self.manager.sync_to_gist())
        menu.addAction(sync_action)

        # 添加从Gist加载功能
        load_action = QAction("从Gist加载", menu)
        load_action.triggered.connect(lambda: self.manager.sync_from_gist())
        menu.addAction(load_action)

        menu.addSeparator()

        delete_action = QAction("删除便签", menu)
        delete_action.triggered.connect(self.close)
        menu.addAction(delete_action)

        menu.exec_(self.mapToGlobal(position))

    def check_edge_position(self):
        if self.dragging or self._grip_resizing or not self.isVisible():
            return

        # 按住 Ctrl 时临时禁用吸附，方便把便签放在靠边但不吸附的位置
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            return

        screen_geo = screen.geometry()
        margin = 50  # Increased from 20px to 50px for easier docking

        x, y = self.x(), self.y()
        width, height = self.width(), self.height()

        new_pos = None
        new_edge = None
        m = CHROME_MARGIN  # 窗口探出屏幕外，让卡片（边距内）贴合屏幕边缘

        # Check left edge
        if x - screen_geo.left() <= margin:
            new_pos = QPoint(screen_geo.left() - m, y)
            new_edge = "left"
        # Check right edge
        elif screen_geo.right() - (x + width) <= margin:
            new_pos = QPoint(screen_geo.right() - width + m, y)
            new_edge = "right"
        # Check top edge
        elif y - screen_geo.top() <= margin:
            new_pos = QPoint(x, screen_geo.top() - m)
            new_edge = "top"
        # Check bottom edge
        elif screen_geo.bottom() - (y + height) <= margin:
            new_pos = QPoint(x, screen_geo.bottom() - height + m)
            new_edge = "bottom"

        if new_pos and new_pos != self.pos():
            self.move(new_pos)
            self.edge_snapped = new_edge
            self.dock_mode = "strip"  # 手动吸附默认细条形态（吸附态）
            self._update_chrome()

    def check_mouse_hover(self):
        if not self.isVisible() or self._grip_resizing:
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
        elif self.is_expanded:
            # 普通状态：悬停显示标题栏，离开自动隐藏（界面更干净）
            buffer = 8
            buffered_geo = self.geometry().adjusted(-buffer, -buffer, buffer, buffer)
            if buffered_geo.contains(cursor_pos):
                if not self.title_bar.isVisible():
                    self.title_bar.show()
            elif self.title_bar.isVisible():
                self.title_bar.hide()

    def get_trigger_zone(self):
        """Get the trigger zone for collapsed notes to prevent jitter"""
        if not self.edge_snapped:
            return self.geometry()

        screen = QApplication.screenAt(self.geometry().center())
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

    def expand(self, animate=True):
        if not self.edge_snapped:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            return

        screen_geo = screen.geometry()
        current_geo = self.geometry()

        self.is_expanded = True
        # 从隐藏态色块恢复：重新接收鼠标事件、显示内容
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if self.content_stack.isHidden():
            self.content_stack.show()
        self.text_edit.setStyleSheet(self._editor_style())
        # 恢复展开状态的滚动条
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        new_width = self.expand_windown_size[0]
        new_height = self.expand_windown_size[1]
        self.set_content(self.full_content)
        m = CHROME_MARGIN  # 窗口探出屏幕外，卡片贴合屏幕边缘
        if self.edge_snapped == "left":
            new_geo = QRect(screen_geo.left() - m, current_geo.y(), new_width, new_height)
        elif self.edge_snapped == "right":
            new_geo = QRect(
                screen_geo.right() - new_width + m,
                current_geo.y(),
                new_width,
                new_height,
            )
        elif self.edge_snapped == "top":
            new_geo = QRect(current_geo.x(), screen_geo.top() - m, new_width, new_height)
        elif self.edge_snapped == "bottom":
            new_geo = QRect(
                current_geo.x(),
                screen_geo.bottom() - new_height + m,
                new_width,
                new_height,
            )
        else:
            return

        if animate:
            self._animate_to(new_geo)
        else:
            self.setGeometry(new_geo)

        # 还原正常显示，清除收缩状态的 tooltip
        self.setToolTip("")
        self.toggle_preview(self.show_preview, change_default=False)
        self._update_chrome()
        self.manager.save_notes()

    def collapse(self):
        if not self.edge_snapped:
            return

        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            return

        self.is_expanded = False
        self.text_edit.setStyleSheet(self._editor_style(padding=0))
        # 收缩态不显示滚动条（否则 Fusion 风格下露出上下箭头按钮）
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        screen_geo = screen.geometry()
        current_geo = self.geometry()

        if self.dock_mode == "sliver":
            new_geo = self._sliver_geometry(screen_geo, current_geo)
        else:
            new_geo = self._strip_geometry(screen_geo, current_geo)

        self._animate_to(new_geo)
        self.toggle_preview(False, change_default=False)
        self._update_chrome()
        # self.manager.save_notes()

    def _mini_display(self):
        """吸附态细条显示的首行文字和卡片宽度（按文字估算）"""
        first_line = self.full_content.split("\n")[0] if self.full_content else ""
        display_text = first_line.strip().lstrip("#*-> ").strip() or "新便签"
        width = 0
        for char in display_text:
            if ord(char) > 127:
                width += 17
            elif char.isupper():
                width += 11
            else:
                width += 9
        return display_text, max(width, self.collapse_windown_size[0])

    def _strip_geometry(self, screen_geo, current_geo):
        """吸附态几何：显示首行的细条（同时恢复内容可见性和事件接收）"""
        display_text, card_width = self._mini_display()
        self.setToolTip("\n".join(self.full_content.split("\n")[:5]))

        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if self.content_stack.isHidden():
            self.content_stack.show()
        self.set_content(display_text)
        # collapse_windown_size 是卡片尺寸，窗口需加上四周边距（探出屏幕外的部分）
        m = CHROME_MARGIN
        new_height = self.collapse_windown_size[1] + 2 * m
        new_width = card_width + 2 * m
        if self.edge_snapped == "left":
            return QRect(screen_geo.left() - m, current_geo.y(), new_width, new_height)
        elif self.edge_snapped == "right":
            return QRect(screen_geo.right() - new_width + m, current_geo.y(), new_width, new_height)
        elif self.edge_snapped == "top":
            return QRect(current_geo.x(), screen_geo.top() - m, current_geo.width(), new_height)
        elif self.edge_snapped == "bottom":
            return QRect(current_geo.x(), screen_geo.bottom() - new_height + m, current_geo.width(), new_height)

    def _sliver_geometry(self, screen_geo, current_geo):
        """隐藏态几何：6px 色块，沿边缘方向的长度与吸附态细条一致；
        纯颜色不显示内容，点击穿透（悬停检测走全局轮询，不依赖窗口接收事件），不遮挡其他应用"""
        self.setToolTip("")
        self.content_stack.hide()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        m = CHROME_MARGIN
        if self.edge_snapped in ("left", "right"):
            # 竖向色块：6px 宽，高度与吸附态细条的高度一致
            new_width = 6 + 2 * m
            new_height = self.collapse_windown_size[1] + 2 * m
            new_y = max(screen_geo.top(), min(current_geo.y(), screen_geo.bottom() - new_height))
            if self.edge_snapped == "left":
                return QRect(screen_geo.left() - m, new_y, new_width, new_height)
            else:
                return QRect(screen_geo.right() - new_width + m, new_y, new_width, new_height)
        else:
            # 横向色块：6px 高，宽度与吸附态细条的宽度一致
            _, card_w = self._mini_display()
            new_width = card_w + 2 * m
            new_height = 6 + 2 * m
            new_x = max(screen_geo.left(), min(current_geo.x(), screen_geo.right() - new_width))
            if self.edge_snapped == "top":
                return QRect(new_x, screen_geo.top() - m, new_width, new_height)
            else:
                return QRect(new_x, screen_geo.bottom() - new_height + m, new_width, new_height)

    def _nearest_edge(self):
        """距离便签中心最近的屏幕边缘"""
        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            return "left"
        geo = screen.geometry()
        c = self.geometry().center()
        dists = {
            "left": abs(c.x() - geo.left()),
            "right": abs(geo.right() - c.x()),
            "top": abs(c.y() - geo.top()),
            "bottom": abs(geo.bottom() - c.y()),
        }
        return min(dists, key=dists.get)

    def dock_to_edge(self):
        """热键隐藏：吸附到最近的屏幕边缘并收缩成 6px 色块（鼠标划过自动展开）"""
        if self.edge_snapped and not self.is_expanded and self.dock_mode == "sliver":
            return  # 已经是隐藏态色块
        # 记住展开尺寸，悬停展开时还原
        if self.is_expanded:
            self.expand_windown_size = [self.width(), self.height()]
        if not self.edge_snapped:
            self.edge_snapped = self._nearest_edge()
        self.dock_mode = "sliver"
        screen = QApplication.screenAt(self.geometry().center())
        if not screen:
            return
        geo = screen.geometry()
        m = CHROME_MARGIN  # 窗口探出屏幕外，色块贴合边缘
        if self.edge_snapped == "left":
            self.move(geo.left() - m, self.y())
        elif self.edge_snapped == "right":
            self.move(geo.right() - self.width() + m, self.y())
        elif self.edge_snapped == "top":
            self.move(self.x(), geo.top() - m)
        elif self.edge_snapped == "bottom":
            self.move(self.x(), geo.bottom() - self.height() + m)
        self.show()
        self.title_bar.hide()
        self.collapse()

    def undock(self, geometry):
        """热键显示：从边缘还原到浮动位置和尺寸（窗口态）"""
        self.edge_snapped = None
        self.dock_mode = "strip"
        self.is_expanded = True
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.text_edit.setStyleSheet(self._editor_style())
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        if self.content_stack.isHidden():
            self.content_stack.show()
        self.set_content(self.full_content)
        self.setGeometry(geometry)
        self.setToolTip("")
        self.toggle_preview(self.show_preview, change_default=False)
        self._update_chrome()
        self.show()
        self.raise_()
        self.activateWindow()
        self.manager.save_notes()

    def restore_strip(self):
        """从隐藏态色块恢复成吸附态细条"""
        self.dock_mode = "strip"
        self.collapse()

    def _in_title_bar(self, pos):
        """窗口坐标是否落在可拖拽的标题区域（适配浮动边距和折叠迷你条）"""
        if not self.title_bar.isVisible():
            # 折叠成迷你条时标题栏隐藏，整个窗口都是拖拽区
            return not self.is_expanded
        title_rect = QRect(self.title_bar.mapTo(self, QPoint(0, 0)), self.title_bar.size())
        return title_rect.contains(pos)

    def eventFilter(self, obj, event):
        # 调整手柄拖动期间：禁用阴影避免重绘残影，并暂停边缘吸附/折叠轮询
        if obj is self.size_grip:
            if event.type() == QEvent.MouseButtonPress:
                self._grip_resizing = True
                self.shadow_effect.setEnabled(False)
            elif event.type() == QEvent.MouseButtonRelease:
                self._grip_resizing = False
                self._update_chrome()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self._in_title_bar(event.pos()):
            # 拖出折叠的迷你条：先无动画还原成完整便签（尺寸就绪后再算拖拽偏移）
            if self.edge_snapped is not None and not self.is_expanded:
                self.expand(animate=False)
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self.dragging = True
            # 脱离吸附的样式变化延迟到真正拖动时（避免单击就突变）
            self._drag_unsnap_pending = self.edge_snapped is not None
            if self.edge_snapped is not None:
                self.edge_snapped = None
                self.is_expanded = True
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self.drag_pos:
            if self._drag_unsnap_pending:
                self._drag_unsnap_pending = False
                self._update_chrome()
            self.move(event.globalPos() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.drag_pos = None
        self.dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        # 双击标题栏：就地折叠/展开（卷帘效果）
        if event.button() == Qt.LeftButton and self._in_title_bar(event.pos()) and not self.edge_snapped:
            self.toggle_shade()
            event.accept()

    def toggle_shade(self):
        """就地折叠成标题栏 / 还原（双击标题栏触发）"""
        if self.edge_snapped:
            return
        if self.is_expanded:
            self.expand_windown_size = [self.width(), self.height()]
            self.is_expanded = False
            self.content_stack.hide()
            self.resize(self.width(), self.title_bar.sizeHint().height() + 2 * CHROME_MARGIN)
        else:
            self.is_expanded = True
            self.content_stack.show()
            self.resize(self.expand_windown_size[0], self.expand_windown_size[1])
        self._update_chrome()
        self.manager.save_notes()

    def insert_timestamp(self):
        """在光标处插入当前时间戳（Ctrl+T）"""
        if self.is_locked:
            return
        self.text_edit.insertPlainText(datetime.now().strftime("%Y-%m-%d %H:%M "))

    def toggle_lock(self, checked):
        """锁定/解锁便签：锁定后内容只读且不可删除"""
        self.is_locked = bool(checked)
        self.text_edit.setReadOnly(self.is_locked)

    def _icon_color(self):
        """图标颜色：根据便签底色亮度适配"""
        lum = 0.299 * self.color.red() + 0.587 * self.color.green() + 0.114 * self.color.blue()
        return QColor("#f0f0f0") if lum < 128 else QColor("#444")

    def _text_color(self):
        """文字颜色：深色便签用浅色字，浅色便签用深色字"""
        lum = 0.299 * self.color.red() + 0.587 * self.color.green() + 0.114 * self.color.blue()
        return "#f5f5f5" if lum < 128 else "#333"

    def _editor_style(self, padding=10):
        return f"""
            QTextEdit {{
                background: transparent;
                border: none;
                padding: {padding}px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto;
                font-size: 14px;
                line-height: 1.5;
                color: {self._text_color()};
            }}
            QTextEdit:focus {{
                outline: none;
            }}
        """

    def _refresh_icons(self):
        """按当前底色和预览状态刷新标题栏图标"""
        color = self._icon_color()
        self.preview_btn.setIcon(make_icon("edit" if self.show_preview else "preview", color=color))
        self.preview_btn.setToolTip("返回编辑" if self.show_preview else "Markdown 预览")
        self.color_btn.setIcon(make_icon("palette", color=color))
        self.close_btn.setIcon(make_icon("close", color=color))
        self.opacity_icon.setPixmap(make_icon("opacity", color=color).pixmap(16, 16))

    def _update_chrome(self):
        """阴影与调整柄状态。边距恒定不在这里切换（吸附时窗口探出屏幕外，
        卡片始终贴合边缘），因此吸附/拖出不会改变卡片尺寸"""
        self.shadow_effect.setEnabled(not self._grip_resizing)
        self.size_grip.setVisible(self.is_expanded)

    def _animate_to(self, target_geo):
        """滑动动画过渡到目标位置尺寸"""
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.OutQuad)
        anim.setStartValue(self.geometry())
        anim.setEndValue(target_geo)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._geometry_anim = anim

    def choose_color(self):
        """预设色卡一键换色，也可打开自定义选色"""
        menu = QMenu(self)
        for hex_color in PRESET_COLORS:
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor(hex_color))
            action = QAction(QIcon(pixmap), "", menu)
            action.triggered.connect(lambda checked=False, h=hex_color: self.set_color(QColor(h)))
            menu.addAction(action)
        menu.addSeparator()
        custom_action = QAction("自定义颜色...", menu)
        custom_action.triggered.connect(self._choose_custom_color)
        menu.addAction(custom_action)
        menu.exec_(QCursor.pos())

    def _choose_custom_color(self):
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
        # 底色变化后同步适配文字与图标颜色
        self.text_edit.setStyleSheet(self._editor_style())
        self._refresh_icons()
        if self.show_preview:
            self.update_preview()
        if not self.manager._loading:
            self.manager.save_notes()

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
        # 仅 cocoa 平台插件下 winId 才是真实 NSView 指针（offscreen 等插件下解引用会崩溃）
        if MACOS_AVAILABLE and QApplication.platformName() == "cocoa":
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
        if not self.manager._loading:
            self.manager.save_notes()

    def set_content(self, content, replace=False):
        if replace:
            self.full_content = content
        self.text_edit.setPlainText(content)
        if self.show_preview:
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
        if self.show_preview:
            self.update_preview()
        self.manager.save_notes()

    def toggle_preview(self, checked, change_default=True):
        if checked:
            if change_default:
                self.show_preview = True
            self.update_preview()  # 切到预览时才渲染
            self.text_edit.hide()
            self.preview_browser.show()
        else:
            if change_default:
                self.show_preview = False
            self.text_edit.show()
            self.preview_browser.hide()
        self._refresh_icons()

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
                            color: {self._text_color()}; 
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
        if not self.manager._loading:
            self.manager.save_notes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.manager._loading:
            self.manager.save_notes()

    def close_without_confirmation(self):
        """直接关闭便签，不显示确认窗口"""
        self.manager.close_note(self.note_id, save=True)
        self.close()

    def closeEvent(self, event):
        if self.is_locked:
            QMessageBox.information(self, "便签已锁定", "便签已锁定，请先在右键菜单中解锁后再删除。")
            event.ignore()
            return

        # 显示确认对话框
        content_preview = (
            self.get_content()[:50] + "..."
            if len(self.get_content()) > 50
            else self.get_content()
        )

        msg_box = QMessageBox()
        msg_box.setWindowTitle("确认删除便签")
        msg_box.setText("确定要删除这个便签吗？")
        msg_box.setInformativeText(f"内容: {content_preview}")
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setIcon(QMessageBox.Question)

        result = msg_box.exec_()

        if result == QMessageBox.Yes:
            self.manager.close_note(self.note_id, save=True)  # 改为True以保存更改
            event.accept()
        else:
            event.ignore()


class GlobalHotkeyManager(QObject):
    """全局快捷键管理器"""
    
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.hotkey_listener = None
        self.is_hidden = False
        
        if GLOBAL_HOTKEYS_AVAILABLE:
            try:
                self.setup_global_hotkeys()
                print("全局快捷键已启用 (Ctrl+Shift+H 显隐, Ctrl+Shift+N 新建)")
            except Exception as e:
                print(f"全局快捷键初始化失败: {e}")
                print("请安装 pynput: pip install pynput")
        else:
            print("全局快捷键不可用，请安装 pynput: pip install pynput")
    
    def setup_global_hotkeys(self):
        """设置全局快捷键"""
        # 使用Qt的线程安全方式调用
        def on_toggle():
            QTimer.singleShot(0, self.toggle_all_notes)

        def on_new_note():
            QTimer.singleShot(0, self.manager.create_new_note)

        # 定义快捷键组合：Ctrl+Shift+H 切换显示/隐藏，Ctrl+Shift+N 新建便签
        hotkeys = [
            keyboard.HotKey(keyboard.HotKey.parse('<ctrl>+<shift>+h'), on_toggle),
            keyboard.HotKey(keyboard.HotKey.parse('<ctrl>+<shift>+n'), on_new_note),
        ]

        def for_canonical(f):
            return lambda k: f(listener.canonical(k))

        listener = keyboard.Listener(
            on_press=for_canonical(lambda k: [hk.press(k) for hk in hotkeys]),
            on_release=for_canonical(lambda k: [hk.release(k) for hk in hotkeys])
        )

        self.hotkey_listener = listener
        listener.start()
    
    def toggle_all_notes(self):
        """切换显示/隐藏所有便签"""
        if self.is_hidden:
            self.manager.show_all_notes()
            self.is_hidden = False
            print("显示所有便签")
        else:
            self.manager.hide_all_notes()
            self.is_hidden = True
            print("隐藏所有便签")
    
    def cleanup(self):
        """清理资源"""
        if self.hotkey_listener:
            self.hotkey_listener.stop()


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

        # 便签列表子菜单：点击直达对应便签
        self.notes_menu = QMenu("便签列表")
        self.notes_menu.aboutToShow.connect(self.rebuild_notes_menu)
        menu.addMenu(self.notes_menu)

        topmost_action = QAction("保持置顶", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(True)
        topmost_action.triggered.connect(self.toggle_always_on_top)
        menu.addAction(topmost_action)

        menu.addSeparator()

        # 添加手动同步功能
        sync_action = QAction("同步到Gist", menu)
        sync_action.triggered.connect(self.manager.sync_to_gist)
        menu.addAction(sync_action)

        load_action = QAction("从Gist加载", menu)
        load_action.triggered.connect(self.manager.sync_from_gist)
        menu.addAction(load_action)

        export_action = QAction("导出为Markdown", menu)
        export_action.triggered.connect(self.manager.export_notes_markdown)
        menu.addAction(export_action)

        menu.addSeparator()

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
        hotkey_msg = " (Ctrl+Shift+H 显隐, Ctrl+Shift+N 新建)" if GLOBAL_HOTKEYS_AVAILABLE else ""
        self.showMessage(
            "PyStickies", 
            f"右键点击托盘图标新建便签{hotkey_msg}", 
            QSystemTrayIcon.Information, 
            3000
        )

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.manager.show_all_notes()

    def toggle_always_on_top(self, checked):
        """切换置顶状态"""
        self.always_on_top = checked
        for note in self.manager.notes.values():
            note.set_always_on_top(checked)

    def rebuild_notes_menu(self):
        """动态生成便签列表（点击直达）"""
        self.notes_menu.clear()
        if not self.manager.notes:
            empty_action = QAction("(无便签)", self.notes_menu)
            empty_action.setEnabled(False)
            self.notes_menu.addAction(empty_action)
            return
        for note_id, note in self.manager.notes.items():
            first_line = (note.get_content() or "").split("\n")[0].strip() or "(空便签)"
            action = QAction(first_line[:30], self.notes_menu)
            action.triggered.connect(lambda checked=False, n=note: self.focus_note(n))
            self.notes_menu.addAction(action)

    def focus_note(self, note):
        """显示并聚焦指定便签"""
        note.show()
        note.raise_()
        note.activateWindow()


def main():
    # 单实例锁：防止重复启动两个实例互相覆盖数据
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pystickies.lock")
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("PyStickies 已经在运行，请勿重复启动")
        sys.exit(0)

    app = QApplication(sys.argv)

    # Set app style for dark mode support
    app.setStyle("Fusion")

    # 纯菜单栏工具形态：隐藏 Dock 图标
    if MACOS_AVAILABLE:
        try:
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        except Exception as e:
            print(f"隐藏 Dock 图标失败: {e}")

    manager = StickyNoteManager()

    # Create system tray
    tray = SystemTrayIcon(manager)
    tray.show()

    # Create initial note if no saved notes
    if not manager.notes:
        print("No saved notes found, creating new note")
        manager.create_new_note()

    # 确保程序退出时保存数据
    app.aboutToQuit.connect(manager.cleanup_and_exit)

    # 响应 SIGTERM（run 脚本 stop 用 kill 终止进程）：Qt 事件循环阻塞在 C++ 层，
    # 靠空定时器让 Python 定期获得执行权，信号处理器才有机会运行
    signal.signal(signal.SIGTERM, lambda signum, frame: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(500)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
