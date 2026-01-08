import sys
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QFontMetrics
from PyQt6.QtCore import QTimer, Qt

class ScrollingStatusBar(QSystemTrayIcon):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        
        # 1. 设置文本和字体 (加大字号)
        self.full_text = text + "      " 
        self.font = QFont(".AppleSystemUIFont", 14) # 稍微调大字体
        self.offset = 0
        
        # 2. 预计算宽度
        metrics = QFontMetrics(self.font)
        self.text_width = metrics.horizontalAdvance(self.full_text)
        
        # 固定显示区域的宽度（你可以根据需要调大这个数值）
        self.display_width = 180 
        
        # 3. 定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self.scroll_text)
        self.timer.start(50) # 调快一点，看起来更顺滑

        self.update_icon()
        
        # 右键菜单
        menu = QMenu()
        menu.addAction("退出", QApplication.quit)
        self.setContextMenu(menu)

    def update_icon(self):
        # --- 适配 Retina 屏幕的关键：Device Pixel Ratio ---
        # macOS 状态栏高度通常是 22px
        logical_height = 22
        # 获取屏幕缩放倍率（通常是 2.0）
        dpr = 2 
        
        # 创建一个 2 倍大小的 Pixmap 以保证清晰度
        pixmap = QPixmap(self.display_width * dpr, logical_height * dpr)
        pixmap.setDevicePixelRatio(dpr) # 核心：告知系统这是高分图
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font)
        
        # 使用 Template Icon 模式，系统会自动处理黑白切换
        painter.setPen(QColor("black")) 

        # 绘制滚动文字
        current_x = -self.offset
        # 这里的 16 是文字基线位置，可根据字号微调
        painter.drawText(current_x, 20, self.full_text)
        
        if current_x + self.text_width < self.display_width:
            painter.drawText(current_x + self.text_width, 20, self.full_text)
            
        painter.end()
        
        # 设置为 Template，这样在深色模式下会自动反色
        icon = QIcon(pixmap)
        icon.setIsMask(True) 
        self.setIcon(icon)

    def scroll_text(self):
        self.offset += 1  # 每次移动 1 像素
        if self.offset >= self.text_width:
            self.offset = 0
        self.update_icon()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    tray = ScrollingStatusBar("正在播放: 告白气球 - 周杰伦 [特别长的一段测试歌词效果]")
    tray.show()
    
    sys.exit(app.exec())