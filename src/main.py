# -*- coding: utf-8 -*-
"""
全国天气监测大屏 (Windows 桌面程序)
功能:
  - 全国城市/区域切换 (支持自动定位)
  - 中央气象台雷达图 / 卫星云图实时展示
  - 每10分钟更新: 降雨情况 / 风速风向 / 天气状况 / 温度湿度
  - 历史趋势曲线 (24小时)
  - 降雨/大风告警提醒
数据源: 中央气象台 + 中国天气网 (公开免费)
运行: python main.py  或打包 exe
"""
import sys
import os
import threading
import time
from datetime import datetime

# 允许无 PyQt6 时也能导入做逻辑测试
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QComboBox, QPushButton, QFrame, QGridLayout,
        QMessageBox
    )
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
    from PyQt6.QtGui import QPixmap, QImage, QIcon, QPainter, QColor
    HAS_QT = True
except Exception:
    HAS_QT = False
    print("[警告] 未安装 PyQt6, 界面不可用 (可用 PyInstaller 打包后运行)")

import weather_data as wd
import history_store as hs
import locator


# ================================================================
# 后台抓取线程
# ================================================================
class FetchWorker(QThread):
    """后台线程: 抓取实时天气, 不阻塞界面"""
    result_ready = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, city_code, city_name, parent=None):
        super().__init__(parent)
        self.city_code = city_code
        self.city_name = city_name

    def run(self):
        try:
            w = wd.get_realtime_weather(self.city_code)
            w["_city_name"] = self.city_name
            w["_city_code"] = self.city_code
            self.result_ready.emit(w)
        except Exception as e:
            self.error.emit(str(e))


class ImageWorker(QThread):
    """后台线程: 抓取雷达/云图"""
    image_ready = pyqtSignal(str, bytes, object)  # type, bytes, dt
    error = pyqtSignal(str)

    def __init__(self, img_type, parent=None):
        super().__init__(parent)
        self.img_type = img_type  # "radar" or "satellite"

    def run(self):
        try:
            if self.img_type == "radar":
                data, dt = wd.get_radar_image()
            else:
                data, dt = wd.get_satellite_image()
            self.image_ready.emit(self.img_type, data, dt)
        except Exception as e:
            self.error.emit(str(e))


# ================================================================
# 趋势折线图控件
# ================================================================
class TrendChart(QWidget):
    """用 QPainter 绘制温度/降雨趋势折线图 (无额外依赖)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.setMinimumHeight(130)
        self.setStyleSheet("background:#f5f7fa;border-radius:6px;")

    def set_data(self, rows):
        self.rows = rows or []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        if not self.rows:
            painter.setPen(QColor("#999"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             "暂无历史数据 (每10分钟自动记录, 1小时后可见趋势)")
            return

        # 温度序列
        temps = [(i, r.get("temp")) for i, r in enumerate(self.rows)
                 if r.get("temp") is not None]
        if temps:
            self._draw_series(painter, rect, temps, "温度(℃)", QColor("#1a73e8"))
        # 降雨序列 (用右侧副轴)
        rains = [(i, r.get("rain")) for i, r in enumerate(self.rows)
                 if r.get("rain")]
        if rains:
            self._draw_series(painter, rect, rains, "降雨(mm)", QColor("#e67e22"))

    def _draw_series(self, painter, rect, points, label, color):
        if len(points) < 2:
            return
        # 坐标范围
        vals = [p[1] for p in points]
        vmin, vmax = min(vals), max(vals)
        if vmax == vmin:
            vmax = vmin + 1
        n = len(self.rows) or 1
        painter.setPen(QColor(color))
        prev = None
        for idx, val in points:
            x = rect.left() + (idx / n) * rect.width()
            y = rect.bottom() - ((val - vmin) / (vmax - vmin)) * rect.height()
            if prev:
                painter.drawLine(prev[0], prev[1], x, y)
            prev = (x, y)
            painter.fillRect(int(x) - 2, int(y) - 2, 5, 5, color)
        painter.setPen(color)
        painter.drawText(rect.right() - 60, rect.top() + 12, label)


# ================================================================
# 主窗口
# ================================================================
class MainWindow(QMainWindow):
    REFRESH_SECONDS = 600  # 每10分钟

    def __init__(self):
        super().__init__()
        self.current_city = "呼和浩特"
        self.current_code = wd.get_city_code(self.current_city)
        self.setWindowTitle("全国天气监测 - 雷达云图/降雨/风速风向")
        self.resize(1100, 760)

        # 告警阈值 (可调)
        self.rain_threshold = 1.0    # 降雨告警 mm
        self.rain24_threshold = 20.0 # 24h降雨告警 mm
        self.wind_threshold = 30.0   # 大风告警 km/h

        self._build_ui()

        # 定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(self.REFRESH_SECONDS * 1000)

        # 启动自动定位 (后台)
        self._auto_locate()

        # 首次加载
        self.refresh_all()

    # ---------- 界面构建 ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ===== 顶部控制栏 =====
        top = QHBoxLayout()
        self.title_label = QLabel("全国天气监测")
        self.title_label.setStyleSheet("font-size:20px;font-weight:bold;color:#1a73e8;")
        top.addWidget(self.title_label)
        top.addSpacing(20)

        top.addWidget(QLabel("地区:"))
        self.city_combo = QComboBox()
        self.city_combo.setMinimumWidth(160)
        self._fill_cities()
        self.city_combo.currentTextChanged.connect(self.on_city_change)
        top.addWidget(self.city_combo)

        self.locate_btn = QPushButton("自动定位")
        self.locate_btn.clicked.connect(self.on_auto_locate_click)
        top.addWidget(self.locate_btn)

        self.refresh_btn = QPushButton("立即刷新")
        self.refresh_btn.clicked.connect(self.refresh_all)
        top.addWidget(self.refresh_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color:#666;")
        top.addWidget(self.status_label)
        top.addStretch()

        root.addLayout(top)

        # ===== 中部: 图表大区 =====
        middle = QHBoxLayout()
        # 左: 雷达/云图
        self.image_panel = QVBoxLayout()
        self.image_label = QLabel("加载中...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background:#0d0d1a;color:#aaa;border-radius:8px;")
        self.image_label.setMinimumHeight(360)
        self.image_panel.addWidget(self.image_label, stretch=3)
        self.image_time_label = QLabel("")
        self.image_time_label.setStyleSheet("color:#888;")
        self.image_panel.addWidget(self.image_time_label)
        # 图像切换按钮
        img_btn_row = QHBoxLayout()
        self.btn_radar = QPushButton("雷达拼图")
        self.btn_radar.clicked.connect(lambda: self._set_image_mode("radar"))
        self.btn_sat = QPushButton("卫星云图")
        self.btn_sat.clicked.connect(lambda: self._set_image_mode("satellite"))
        img_btn_row.addWidget(self.btn_radar)
        img_btn_row.addWidget(self.btn_sat)
        self.image_panel.addLayout(img_btn_row)
        middle.addLayout(self.image_panel, stretch=3)

        # 右: 实时指标卡
        middle.addLayout(self._make_metrics_panel(), stretch=2)
        root.addLayout(middle, stretch=3)

        # ===== 底部: 历史趋势 =====
        bottom = QVBoxLayout()
        bottom.addWidget(QLabel("历史趋势 (近24小时):"))
        self.trend_chart = TrendChart()
        bottom.addWidget(self.trend_chart)
        self.trend_info = QLabel("")
        self.trend_info.setStyleSheet("color:#888;")
        bottom.addWidget(self.trend_info)
        root.addLayout(bottom, stretch=2)

        # 告警条
        self.alert_label = QLabel("")
        self.alert_label.setStyleSheet("color:#c0392b;font-weight:bold;")
        root.addWidget(self.alert_label)

        # 图片模式
        self.image_mode = "radar"

    def _make_metrics_panel(self):
        """右侧实时指标卡"""
        panel = QVBoxLayout()
        panel.addWidget(QLabel("实时天气实况"))
        grid = QGridLayout()
        self.m_temp = self._metric_cell("温度")
        self.m_weather = self._metric_cell("天气")
        self.m_rain = self._metric_cell("降雨")
        self.m_wind = self._metric_cell("风速风向")
        self.m_humidity = self._metric_cell("湿度")
        self.m_pressure = self._metric_cell("气压")
        grid.addWidget(self.m_temp["box"], 0, 0)
        grid.addWidget(self.m_weather["box"], 0, 1)
        grid.addWidget(self.m_rain["box"], 1, 0)
        grid.addWidget(self.m_wind["box"], 1, 1)
        grid.addWidget(self.m_humidity["box"], 2, 0)
        grid.addWidget(self.m_pressure["box"], 2, 1)
        panel.addLayout(grid)
        # 更新时间
        self.update_time_label = QLabel("")
        self.update_time_label.setStyleSheet("color:#888;")
        panel.addWidget(self.update_time_label)
        panel.addStretch()
        return panel

    def _metric_cell(self, name):
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        box.setStyleSheet("QFrame{background:#fff;border:1px solid #e0e0e0;border-radius:8px;}")
        v = QVBoxLayout(box)
        name_l = QLabel(name)
        name_l.setStyleSheet("color:#888;font-size:12px;")
        val_l = QLabel("--")
        val_l.setStyleSheet("font-size:20px;font-weight:bold;color:#1a1a1a;")
        v.addWidget(name_l)
        v.addWidget(val_l)
        return {"box": box, "val": val_l}

    # ---------- 城市填充 ----------
    def _fill_cities(self):
        self.city_combo.clear()
        commons = ["呼和浩特", "清水河县", "北京", "上海", "广州", "深圳",
                   "天津", "重庆", "包头", "鄂尔多斯", "乌海", "济南",
                   "武汉", "西安", "成都", "杭州", "南京"]
        seen = set()
        for c in commons:
            if c in wd.CITY_FLAT:
                self.city_combo.addItem(c)
                seen.add(c)
        others = sorted([c for c in wd.CITY_FLAT if c not in seen])
        for c in others[:300]:
            self.city_combo.addItem(c)
        idx = self.city_combo.findText(self.current_city)
        if idx >= 0:
            self.city_combo.setCurrentIndex(idx)

    # ---------- 自动定位 ----------
    def _auto_locate(self):
        def run():
            try:
                loc = locator.locate_by_ip()
                if loc:
                    code = locator.match_city_code(
                        loc.get("city"), loc.get("province"), wd.CITY_FLAT)
                    if code:
                        name = next((n for n, c in wd.CITY_FLAT.items() if c == code),
                                    loc.get("city"))
                        self._apply_location(name, code)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def on_auto_locate_click(self):
        """手动触发定位"""
        self.status_label.setText("正在定位...")
        self._auto_locate()

    def _apply_location(self, name, code):
        try:
            idx = self.city_combo.findText(name)
            if idx >= 0:
                self.city_combo.setCurrentIndex(idx)
            else:
                self.city_combo.addItem(name)
                self.city_combo.setCurrentIndex(self.city_combo.count() - 1)
            self.current_city = name
            self.current_code = code
            self.status_label.setText(f"已自动定位到: {name}")
            self.refresh_all()
        except Exception:
            pass

    # ---------- 事件 ----------
    def on_city_change(self, text):
        if not text:
            return
        self.current_city = text
        self.current_code = wd.get_city_code(text)
        if self.current_code:
            self.refresh_all()

    def _set_image_mode(self, mode):
        self.image_mode = mode
        self._load_image()

    # ---------- 数据刷新 ----------
    def refresh_all(self):
        self._fetch_weather()
        self._load_image()

    def _fetch_weather(self):
        if not self.current_code:
            return
        self.status_label.setText("正在获取天气数据...")
        self.worker = FetchWorker(self.current_code, self.current_city)
        self.worker.result_ready.connect(self._on_weather)
        self.worker.error.connect(lambda e: self.status_label.setText(f"天气获取失败: {e}"))
        self.worker.start()

    def _on_weather(self, data):
        city = data.get("_city_name", self.current_city)
        self.m_temp["val"].setText(f"{data.get('temp','--')} ℃")
        self.m_weather["val"].setText(data.get("weather", "--"))
        self.m_humidity["val"].setText(f"{data.get('humidity','--')} %")
        self.m_pressure["val"].setText(f"{data.get('pressure','--')} hPa")
        wind = f"{data.get('wind_dir','')} {data.get('wind_level','')}"
        if data.get("wind_speed"):
            wind += f" ({data['wind_speed']}/h)"
        self.m_wind["val"].setText(wind or "--")
        rain_level, flag = wd.analyze_rain(data)
        self.m_rain["val"].setText(rain_level)
        if data.get("rain24h"):
            self.m_rain["val"].setText(
                f"{rain_level} | 24h:{data['rain24h']}mm")

        self.update_time_label.setText(f"更新时间: {data.get('time','')}")

        self._check_alerts(data, rain_level)

        try:
            hs.save_snapshot(city, self.current_code, data)
        except Exception:
            pass
        self.status_label.setText(f"已更新: {city} {datetime.now().strftime('%H:%M:%S')}")
        self._update_trend()

    def _check_alerts(self, data, rain_level):
        alerts = []
        rain = data.get("rain") or 0
        if rain and rain > self.rain_threshold:
            alerts.append(f"⚠ 当前降雨 {rain}mm (超{self.rain_threshold}mm)")
        r24 = data.get("rain24h") or 0
        if r24 and r24 > self.rain24_threshold:
            alerts.append(f"⚠ 24小时降雨 {r24}mm (注意防范)")
        wspeed = data.get("wind_speed")
        if wspeed and wspeed > self.wind_threshold:
            alerts.append(f"⚠ 大风: {data.get('wind_dir','')} {wspeed}km/h (超{self.wind_threshold}km/h)")
        if alerts:
            self.alert_label.setText(" | ".join(alerts))
            self._show_notification(alerts[0])
        else:
            self.alert_label.setText("")

    def _show_notification(self, msg):
        try:
            self.alert_label.setText(msg)
            import winsound
            winsound.MessageBeep(winsound.MB_ICONWARNING)
        except Exception:
            pass

    # ---------- 图片加载 ----------
    def _load_image(self):
        self.image_label.setText("正在加载图片...")
        self.image_label.setStyleSheet(
            "background:#0d0d1a;color:#aaa;border-radius:8px;")
        self.img_worker = ImageWorker(self.image_mode)
        self.img_worker.image_ready.connect(self._on_image)
        self.img_worker.error.connect(
            lambda e: self.image_label.setText(f"图片获取失败: {e}"))
        self.img_worker.start()

    def _on_image(self, img_type, data, dt):
        try:
            pix = QPixmap()
            pix.loadFromData(data)
            w = self.image_label.width() or 600
            h = self.image_label.height() or 360
            self.image_label.setPixmap(
                pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))
            self.image_label.setStyleSheet(
                "background:#0d0d1a;border-radius:8px;")
            self.image_time_label.setText(
                f"数据时间: {dt.strftime('%Y-%m-%d %H:%M')}  (中央气象台)")
        except Exception as e:
            self.image_label.setText(f"图片显示失败: {e}")

    # ---------- 历史趋势 ----------
    def _update_trend(self):
        try:
            rows = hs.get_history(self.current_city, hours=24)
            self.trend_chart.set_data(rows)
            if rows:
                temps = [r["temp"] for r in rows if r["temp"] is not None]
                rains = [r["rain"] for r in rows if r["rain"]]
                t_now = temps[-1] if temps else "--"
                info = f"共{len(rows)}条 | 当前温度 {t_now}℃"
                if rains:
                    info += f" | 有降雨记录 {len(rains)} 次"
                else:
                    info += " | 无降雨"
                self.trend_info.setText(info)
            else:
                self.trend_info.setText("运行10分钟后开始记录, 1小时后可见完整趋势")
        except Exception as e:
            self.trend_info.setText(f"趋势加载失败: {e}")


# ================================================================
# 入口
# ================================================================
def main():
    if not HAS_QT:
        print("未安装 PyQt6, 无法启动界面。")
        print("请先安装: pip install PyQt6  (或直接使用打包好的 exe)")
        return
    app = QApplication(sys.argv)
    app.setApplicationName("全国天气监测")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
