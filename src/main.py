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
    from PyQt6.QtCore import Qt, QTimer, QThread, QRect, pyqtSignal
    from PyQt6.QtGui import QPixmap, QImage, QIcon, QPainter, QColor
    HAS_QT = True
except Exception:
    HAS_QT = False
    print("[警告] 未安装 PyQt6, 界面不可用 (可用 PyInstaller 打包后运行)")

import weather_data as wd
import history_store as hs
import locator


# ================================================================
# 数据获取 (主线程同步执行, 避免 QThread 竞态崩溃)
# ================================================================
def fetch_weather(city_code, city_name):
    """同步抓取实时天气, 返回 dict (含城市名/代码)"""
    w = wd.get_realtime_weather(city_code)
    w["_city_name"] = city_name
    w["_city_code"] = city_code
    return w


def fetch_image(img_type):
    """同步抓取雷达/云图, 返回 (data_bytes, dt)"""
    if img_type == "radar":
        return wd.get_radar_image()
    else:
        return wd.get_satellite_image()


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

        # 启动自动定位 (首次运行时询问授权)
        self._auto_locate()

        # 首次加载: 延迟到窗口显示后, 避免控件未就绪时同步抓取导致崩溃
        QTimer.singleShot(200, self.refresh_all)

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

        self.select_btn = QPushButton("选择地区")
        self.select_btn.clicked.connect(self._open_region_select)
        top.addWidget(self.select_btn)

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
        self.anim_btn = QPushButton("▶ 播放动画")
        self.anim_btn.clicked.connect(self._toggle_anim)
        img_btn_row.addWidget(self.btn_radar)
        img_btn_row.addWidget(self.btn_sat)
        img_btn_row.addWidget(self.anim_btn)
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
        # 首次运行: 询问用户是否允许自动定位 (授权)
        from PyQt6.QtWidgets import QMessageBox
        # 用一个成员标记避免每次刷新都弹
        if getattr(self, "_locate_asked", False):
            return
        self._locate_asked = True
        box = QMessageBox(self)
        box.setWindowTitle("定位授权")
        box.setText("是否允许程序自动定位到您所在城市？\n"
                    "(基于网络IP定位, 若定位不准可在顶部点击'选择地区'手动选择)")
        allow_btn = box.addButton("允许定位", QMessageBox.ButtonRole.AcceptRole)
        deny_btn = box.addButton("暂不定位", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(allow_btn)
        box.exec()
        if box.clickedButton() is not allow_btn:
            return
        # 主线程同步定位 (定位请求快, 一次失败静默)
        try:
            loc = locator.locate_by_ip()
            if loc:
                code = locator.match_city_code(
                    loc.get("city"), loc.get("province"), wd.CITY_FLAT)
                if code:
                    name = next((n for n, c in wd.CITY_FLAT.items() if c == code),
                                loc.get("city"))
                    self._apply_location(name, code)
                else:
                    self.status_label.setText(f"定位到 {loc.get('city')} 但未匹配城市库, 可手动选择")
        except Exception:
            pass

    def on_auto_locate_click(self):
        """手动触发定位"""
        self.status_label.setText("正在定位...")
        self._auto_locate()

    def _open_region_select(self):
        """打开 省-市 选择对话框, 手动精确选地区"""
        from PyQt6.QtWidgets import QDialog, QComboBox, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("选择地区")
        dlg.setMinimumWidth(360)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("请选择省份和城市:"))

        # 省下拉 (prov_cities 按 key)
        prov_combo = QComboBox()
        provs = list(wd.PROV_CITIES.keys())
        provs.sort()
        prov_combo.addItems(provs)
        lay.addWidget(prov_combo)

        # 市下拉
        city_combo = QComboBox()
        lay.addWidget(city_combo)

        def update_cities():
            city_combo.clear()
            p = prov_combo.currentText()
            for c in wd.PROV_CITIES.get(p, []):
                city_combo.addItem(c)
        prov_combo.currentTextChanged.connect(lambda _: update_cities())
        update_cities()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        lay.addWidget(buttons)

        def on_ok():
            city = city_combo.currentText()
            if city:
                code = wd.get_city_code(city)
                if code:
                    self._apply_location(city, code)
                    self.status_label.setText(f"已选择: {city}")
            dlg.accept()
        buttons.accepted.connect(on_ok)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

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
        try:
            data = fetch_weather(self.current_code, self.current_city)
            self._on_weather(data)
        except Exception as e:
            self.status_label.setText("天气获取失败: " + str(e))

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

    # ---------- 图片加载 (云图动态播放) ----------
    def _load_image(self):
        self.image_label.setText("正在加载图片...")
        self.image_label.setStyleSheet(
            "background:#0d0d1a;color:#aaa;border-radius:8px;")
        try:
            data, dt = fetch_image(self.image_mode)
            self._add_frame(self.image_mode, data, dt)
            self._show_frame()
        except Exception as e:
            self.image_label.setText("图片获取失败: " + str(e))

    def _add_frame(self, img_type, data, dt):
        """把一帧加入动画序列 (自累积)"""
        if not hasattr(self, "_frame_list"):
            self._frame_list = []
        # 去重: 相同时间戳不重复添加
        key = dt.strftime("%Y%m%d%H%M")
        if self._frame_list and self._frame_list[-1].get("key") == key:
            return
        self._frame_list.append({"key": key, "data": data, "dt": dt})
        # 最多保留 12 帧 (12小时)
        if len(self._frame_list) > 12:
            self._frame_list.pop(0)
        # 更新动画按钮状态与提示
        if hasattr(self, "anim_btn"):
            n = len(self._frame_list)
            self.anim_btn.setText(f"▶ 播放({n}帧)")

    def _show_frame(self):
        """显示动画序列当前帧"""
        if not hasattr(self, "_frame_list") or not self._frame_list:
            return
        idx = getattr(self, "_frame_idx", -1)
        idx = (idx + 1) % len(self._frame_list)
        self._frame_idx = idx
        fr = self._frame_list[idx]
        try:
            pix = QPixmap()
            pix.loadFromData(fr["data"])
            w = self.image_label.width() or 600
            h = self.image_label.height() or 360
            self.image_label.setPixmap(
                pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation))
            self.image_label.setStyleSheet(
                "background:#0d0d1a;border-radius:8px;")
            self.image_time_label.setText(
                f"数据时间: {fr['dt'].strftime('%Y-%m-%d %H:%M')}  (中央气象台)")
        except Exception as e:
            self.image_label.setText("图片显示失败: " + str(e))

    def _on_image(self, img_type, data, dt):
        # 兼容旧调用: 添加并显示
        self._add_frame(img_type, data, dt)
        self._show_frame()

    def _toggle_anim(self):
        """播放/暂停云图动画"""
        if getattr(self, "_anim_timer", None) and self._anim_timer.isActive():
            self._anim_timer.stop()
            self.anim_btn.setText("▶ 播放")
        else:
            if not hasattr(self, "_anim_timer"):
                self._anim_timer = QTimer(self)
                self._anim_timer.timeout.connect(self._show_frame)
                self._anim_timer.start(1200)  # 1.2秒切换一帧
            else:
                self._anim_timer.start(1200)
            n = len(getattr(self, "_frame_list", []))
            self.anim_btn.setText(f"⏸ 暂停({n}帧)")

    # ---------- 历史趋势 ----------
    def _update_trend(self):
        # 趋势信息: 用文本表逐小时展示温度/风力/降雨 (稳定可靠, 不依赖绘图)
        try:
            rows = hs.get_history(self.current_city, hours=24)
            if not rows:
                self.trend_info.setText(
                    "运行10分钟后开始记录, 1小时后可见逐小时趋势")
                return
            lines = []
            temps = [r["temp"] for r in rows if r["temp"] is not None]
            winds = [r["wind_speed"] for r in rows if r.get("wind_speed") is not None]
            rains = [r["rain"] for r in rows if r.get("rain")]
            # 最新一条
            latest = rows[-1]
            hm = latest.get("ts", "")[11:16] if latest.get("ts") else ""
            l_t = latest.get("temp", "--")
            l_w = latest.get("wind_speed", "--")
            l_r = latest.get("rain", 0) or 0
            lines.append(
                f"最新({hm}): 温度 {l_t}℃ | 风力 {l_w}km/h | 降雨 {l_r}mm")
            # 近6条逐小时明细
            lines.append("—— 逐小时明细 ——")
            for r in rows[-6:]:
                h = r.get("ts", "")[11:16] if r.get("ts") else "--"
                t = r.get("temp", "--")
                w = r.get("wind_speed", "--")
                rain = r.get("rain", 0) or 0
                wd_str = r.get("wind_dir", "")
                wl_str = r.get("wind_level", "")
                lines.append(
                    f"{h}  温度{t}℃  风{w}km/h({wd_str}{wl_str})  降雨{rain}mm")
            self.trend_info.setText("\n".join(lines))
            # 保留图表组件(不再绘制, 避免渲染崩溃), 显示汇总
            self.trend_chart.set_data([])
        except Exception as e:
            self.trend_info.setText("趋势加载失败: " + str(e))


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
