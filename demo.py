import sys
import pyautogui

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PyQt5.QtGui import QColor
import objc
from AppKit import (
    NSApplication,
    NSApp,
    NSWindow,
    NSFloatingWindowLevel,
    NSMainMenuWindowLevel,
    NSStatusWindowLevel,
)


class EdgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # 基础窗口设置
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            # | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: rgba(45, 45, 45, 0.6); border-radius: 8px;")

        # 内容区域设置
        content_widget = QWidget()
        layout = QVBoxLayout()
        self.buttons = ["剪切板1", "快捷动作2", "收藏项3"]

        for btn_text in self.buttons:
            btn = QPushButton(btn_text)
            btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255, 255, 255, 0.85);
                    padding: 12px;
                    border-radius: 6px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background: rgba(200, 230, 255, 0.95);
                }
            """
            )
            layout.addWidget(btn)

        content_widget.setLayout(layout)
        self.setCentralWidget(content_widget)

        # 窗口状态变量
        self.visible_width = 300  # 展开后的宽度
        self.trigger_width = 20  # 吸附状态的可见宽度
        self.is_expanded = False
        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(300)  # 动画时长300ms
        self.animation.setEasingCurve(QEasingCurve.OutQuad)  # 添加缓动曲线

        # 获取主屏幕尺寸
        self.screen = QApplication.primaryScreen().geometry()
        self.reset_position()

        # 鼠标检测定时器
        self.mouse_timer = QTimer()
        self.mouse_timer.timeout.connect(self.check_mouse_position)
        self.mouse_timer.start(50)  # 每50ms检测一次

        # 确保窗口置顶的定时器
        # self.topmost_timer = QTimer()
        # self.topmost_timer.timeout.connect(self.ensure_topmost)
        # self.topmost_timer.start(1000)  # 每1秒检查一次

    # def showEvent(self, event):
    #     super().showEvent(event)
    #     win_id = int(self.winId())
    #     ns_view = objc.objc_object(c_void_p=win_id)
    #     ns_window = ns_view.window()
    #     ns_window.setLevel_(NSStatusWindowLevel)

    def ensure_topmost(self):
        # 如果窗口不在最顶层，重新设置标志
        # if not self.windowFlags() & Qt.WindowStaysOnTopHint:
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.show()

    def reset_position(self):
        # 初始定位到屏幕右侧边缘
        self.setGeometry(
            self.screen.width() - self.trigger_width,  # x坐标
            self.screen.height() // 4,  # y坐标（垂直居中）
            self.visible_width,  # 宽度
            self.screen.height() // 2,  # 高度
        )

    def check_mouse_position(self):
        mouse_x, mouse_y = pyautogui.position()
        win_geo = self.geometry()

        # 定义热区范围（右侧20px）:cite[1]
        hotspot = QRect(
            self.screen.width() - 40,  # 扩展热区宽度到40px提高灵敏度
            win_geo.y(),
            40,
            win_geo.height(),
        )

        # 鼠标进入热区且窗口未展开
        if hotspot.contains(mouse_x, mouse_y) and not self.is_expanded:
            self.expand_window()

        # 鼠标离开窗口且已展开
        elif not win_geo.contains(mouse_x, mouse_y) and self.is_expanded:
            self.collapse_window()

    def expand_window(self):
        self.is_expanded = True
        self.animation.stop()
        self.animation.setStartValue(self.geometry())
        self.animation.setEndValue(
            QRect(
                self.screen.width() - self.visible_width,  # 完全滑入后的x位置
                self.geometry().y(),
                self.visible_width,
                self.geometry().height(),
            )
        )
        self.animation.start()

    def collapse_window(self):
        self.is_expanded = False
        self.animation.stop()
        self.animation.setStartValue(self.geometry())
        self.animation.setEndValue(
            QRect(
                self.screen.width() - self.trigger_width,
                self.geometry().y(),
                self.visible_width,
                self.geometry().height(),
            )
        )
        self.animation.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EdgeWindow()
    window.show()
    sys.exit(app.exec_())
