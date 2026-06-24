"""镜牢统计可视化组件 —— 在应用内查看图表"""
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import ScrollArea, SubtitleLabel, TitleLabel, isDarkTheme
from qfluentwidgets import FluentIcon as FIF
from qframelesswindow import FramelessDialog, StandardTitleBar

from app.language_manager import LanguageManager

# 模块级缓存，供 farming_interface 获取图表文件列表
_cached_charts: list[str] = []


def cache_charts(chart_files: list[str]):
    global _cached_charts
    _cached_charts = list(chart_files)


def get_cached_charts() -> list[str]:
    return _cached_charts


class MirrorStatsDialog(FramelessDialog):
    """镜牢统计图表查看器"""

    def __init__(self, parent, chart_files: list[str]):
        super().__init__(parent)
        self.setObjectName("MirrorStatsDialog")
        LanguageManager().register_component(self)
        self.setWindowTitle(self.tr("镜牢统计数据"))
        self.resize(900, 650)
        self.setMinimumSize(700, 500)

        self.setTitleBar(StandardTitleBar(self))
        self.titleBar.raise_()
        self.titleBar.minBtn.hide()
        self.titleBar.maxBtn.hide()

        self.chart_files = chart_files
        self._current_index = 0
        self._image_labels: list[QLabel] = []

        self._init_ui()
        if chart_files:
            self._show_chart(0)

    def _init_ui(self):
        bg = "rgba(28,28,28,1)" if isDarkTheme() else "rgba(255,255,255,1)"
        self.setStyleSheet(f"MirrorStatsDialog{{background:{bg};}}")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 32, 0, 0)
        main_layout.setSpacing(0)

        # 滚动区域
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(24, 24, 24, 24)
        self.content_layout.setSpacing(16)

        # 标题
        self.title_label = TitleLabel(self.tr("镜牢统计报告"), self.content)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(self.title_label)

        # 图表切换提示
        self.nav_label = SubtitleLabel("", self.content)
        self.nav_label.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(self.nav_label)

        # 图表显示区
        self.chart_container = QWidget(self.content)
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.chart_layout.setSpacing(16)

        self.chart_image = QLabel(self.chart_container)
        self.chart_image.setAlignment(Qt.AlignCenter)
        self.chart_image.setMinimumHeight(400)
        self.chart_image.setStyleSheet("background:transparent;")
        self.chart_layout.addWidget(self.chart_image)

        self.content_layout.addWidget(self.chart_container)
        self.content_layout.addStretch()

        self.scroll.setWidget(self.content)
        main_layout.addWidget(self.scroll)

    def _show_chart(self, index: int):
        if index < 0 or index >= len(self.chart_files):
            return
        self._current_index = index
        filepath = self.chart_files[index]
        pixmap = QPixmap(filepath)
        if not pixmap.isNull():
            scaled = pixmap.scaled(800, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.chart_image.setPixmap(scaled)
        self.nav_label.setText(self.tr(f"图表 {index + 1} / {len(self.chart_files)}  |  滚轮切换"))

    def retranslateUi(self):
        """i18n 翻译回调（LanguageManager 要求）"""
        self.setWindowTitle(self.tr("镜牢统计数据"))
        if hasattr(self, 'title_label') and self.title_label:
            self.title_label.setText(self.tr("镜牢统计报告"))
        if self.chart_files:
            self.nav_label.setText(self.tr(f"图表 {self._current_index + 1} / {len(self.chart_files)}  |  滚轮切换"))

    def wheelEvent(self, event):
        """滚轮切换图表"""
        if not self.chart_files:
            return
        delta = event.angleDelta().y()
        if delta > 0:
            self._show_chart(self._current_index - 1)
        elif delta < 0:
            self._show_chart(self._current_index + 1)
