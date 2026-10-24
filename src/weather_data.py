# -*- coding: utf-8 -*-
"""
气象数据抓取模块
数据来源:
  - 雷达拼图 / 卫星云图: 中央气象台 image.nmc.cn (公开、无登录、每6~10分钟更新)
  - 实时天气/风速风向/降雨/天气状况: 中国天气网 d1.weather.com.cn (中国气象局旗下)
"""
import os
import re
import time
import json
import threading
import requests
from datetime import datetime, timedelta

# ---------- 全国城市代码库 ----------
def _resource_path(rel):
    """兼容 PyInstaller 打包后的资源路径"""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

_CITIES_FILE = _resource_path("china_cities.json")

# 别名归一化: 常见简称 -> 城市库标准名
_ALIASES = {
    "清水河县": "清水河", "赛罕区": "呼和浩特",
    "呼市": "呼和浩特", "呼和浩特市": "呼和浩特",
    "北京市": "北京", "上海市": "上海",
    "天津市": "天津", "重庆市": "重庆", "广州市": "广州", "深圳市": "深圳",
}

# 市级代码映射: 部分市级需要手动补全为8位(取第一个区县代码)
# 自动从 city_children 派生: 若市级无区县则用 省码+市码+01
def _load_cities():
    """加载全国城市库, 返回 (city_flat, city_children, prov_cities)"""
    if not os.path.exists(_CITIES_FILE):
        return {}, {}, {}
    try:
        with open(_CITIES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("city_flat", {}),
                data.get("city_children", {}),
                data.get("prov_cities", {}))
    except Exception:
        return {}, {}, {}

CITY_FLAT, CITY_CHILDREN, PROV_CITIES = _load_cities()

# 常用城市快捷访问 (供界面默认展示)
CITIES = {k: v for k, v in CITY_FLAT.items() if k in
          ("北京", "上海", "广州", "深圳", "天津", "重庆",
           "呼和浩特", "清水河", "包头", "鄂尔多斯", "乌海",
           "济南", "武汉", "西安", "成都", "杭州", "南京")}

# 补充市级完整代码: 市级标准代码 = 省码(5位)+市码(2位)+'01' (8位)
#  - 有区县的市级直接用 children 中同名区县代码
#  - 无同名区县时, 从现有 7位(省+市) 补 '01'
_DIRECT_MUNI = {"北京":"101010100","上海":"101020100","天津":"101030100","重庆":"101040100"}
for cname, ccode in list(CITY_FLAT.items()):
    if cname in _DIRECT_MUNI:
        CITY_FLAT[cname] = _DIRECT_MUNI[cname]
        continue
    kids = CITY_CHILDREN.get(cname, {})
    same = kids.get(cname)
    if same:
        CITY_FLAT[cname] = same
    elif len(ccode) == 8 and ccode.endswith("0"):
        CITY_FLAT[cname] = ccode[:-1] + "01"
    elif len(ccode) == 7:
        CITY_FLAT[cname] = ccode + "01"


# 气象数据图缓存目录
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".weather_monitor_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

# 会话复用
_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
})
_session_lock = threading.Lock()


def _get(url, referer=None, timeout=12, retries=2):
    """带重试的 GET 请求"""
    headers = {}
    if referer:
        headers["Referer"] = referer
    last_err = None
    for i in range(retries + 1):
        try:
            with _session_lock:
                resp = _session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp
            last_err = f"HTTP {resp.status_code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"请求失败 {url}: {last_err}")


# ---------- 实时天气实况 ----------
def get_realtime_weather(city_code):
    """
    获取实时天气实况
    返回: dict (temp/weather/wind_dir/wind_level/humidity/rain/aqi/time 等)
    """
    url = f"https://d1.weather.com.cn/sk_2d/{city_code}.html"
    resp = _get(url, referer="https://www.weather.com.cn/")
    # 接口实际返回 UTF-8; 显式按 UTF-8 解码, 避免 requests 误判为 latin-1 导致中文乱码
    text = resp.content.decode("utf-8", errors="replace")
    # 定位 dataSK= 后的第一个 '{'，做括号配平提取完整 JSON 对象
    start = text.find("dataSK=")
    if start < 0:
        raise RuntimeError("实时天气接口返回格式异常")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError("实时天气接口返回格式异常")
    depth = 0
    end = brace
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    data = json.loads(text[brace:end])
    return {
        "cityname": data.get("cityname", ""),
        "temp": _num(data.get("temp")),        # 温度 ℃
        "weather": data.get("weather", ""),     # 天气现象
        "wind_dir": data.get("WD", ""),         # 风向
        "wind_level": data.get("WS", ""),       # 风力等级
        "wind_speed": _num(re.sub(r"[^0-9.+-]", "", data.get("wse", "") or "")),  # 风速 km/h -> 数字
        "humidity": _num(data.get("SD", "").replace("%", "")),  # 相对湿度 %
        "pressure": _num(data.get("qy", "")),   # 气压 hPa
        "rain": _num(data.get("rain", "")),     # 当前降雨 (mm)
        "rain24h": _num(data.get("rain24h", "")),  # 24h降雨
        "aqi": _num(data.get("aqi", "")),       # 空气质量指数
        "time": data.get("time", ""),           # 更新时间
    }


def _num(v):
    """安全转数字"""
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s or s in ("-", "暂无实况"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------- 雷达拼图 ----------
def get_radar_image(cache_time=None, is_local=False):
    """
    获取中央气象台全国雷达组合反射率拼图(最新一张)
    返回: (图片字节, 时间字符串)
    """
    # 先从雷达页面解析最新时间戳, 再拼图片URL
    page_url = "https://www.nmc.cn/publish/radar/chinaall.html"
    resp = _get(page_url)
    # 匹配形如 20261024150600000 的时间戳
    ts_list = re.findall(r'(20\d{2}/\d{1,2}/\d{1,2}/RDCP/medium/'
                         r'SEVP_AOC_RDCP_SLDAS3_ECREF_ACHN_L88_PI_(\d{17})\.PNG)', resp.text)
    if not ts_list:
        raise RuntimeError("雷达时间戳解析失败")
    # 取最新时间戳
    ts_str = ts_list[0][1]
    dt = datetime.strptime(ts_str, "%Y%m%d%H%M%S%f")
    date_part = dt.strftime("%Y/%m/%d")
    img_url = ("https://image.nmc.cn/product/"
               f"{date_part}/RDCP/medium/"
               f"SEVP_AOC_RDCP_SLDAS3_ECREF_ACHN_L88_PI_{ts_str}.PNG")
    img_resp = _get(img_url, referer=page_url)
    return img_resp.content, dt


# ---------- 卫星云图 ----------
def get_satellite_image():
    """
    获取中央气象台 FY 卫星云图(可见光, 最新一张)
    返回: (图片字节, 时间字符串)
    """
    page_url = "https://www.nmc.cn/publish/satellite/fy2c-disc-vis.html"
    resp = _get(page_url)
    # 匹配云图时间戳
    ts_list = re.findall(r'product/(20\d{2}/\d{1,2}/\d{1,2}/WXBL/medium/'
                         r'SEVP_NSMC_WXBL_FY2G_EVL_ACHN_LNO_PY_(\d{17})\.JPG)', resp.text)
    if not ts_list:
        raise RuntimeError("云图时间戳解析失败")
    ts_str = ts_list[0][1]  # 17位: 14位时间 + 000
    ts_base = ts_str[:14]
    dt = datetime.strptime(ts_base, "%Y%m%d%H%M%S")
    date_part = dt.strftime("%Y/%m/%d")
    img_url = ("https://image.nmc.cn/product/"
               f"{date_part}/WXBL/medium/"
               f"SEVP_NSMC_WXBL_FY2G_EVL_ACHN_LNO_PY_{ts_str}.JPG")
    img_resp = _get(img_url, referer=page_url)
    return img_resp.content, dt


def get_satellite_frames(max_hours=12):
    """
    获取卫星云图最近 N 小时的历史帧序列 (逐小时, 用于动态播放)
    返回: [{"data": bytes, "dt": datetime}, ...] 最新在前, 已去重
    """
    page_url = "https://www.nmc.cn/publish/satellite/fy2c-disc-vis.html"
    resp = _get(page_url)
    ts_list = re.findall(r'product/(20\d{2}/\d{1,2}/\d{1,2}/WXBL/medium/'
                         r'SEVP_NSMC_WXBL_FY2G_EVL_ACHN_LNO_PY_(\d{17})\.JPG)', resp.text)
    if not ts_list:
        raise RuntimeError("云图时间戳解析失败")

    frames = []
    seen = set()
    for date_part, ts_str in ts_list:
        if ts_str in seen:
            continue
        seen.add(ts_str)
        ts_base = ts_str[:14]
        try:
            dt = datetime.strptime(ts_base, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        img_url = ("https://image.nmc.cn/product/"
                   f"{date_part}/WXBL/medium/"
                   f"SEVP_NSMC_WXBL_FY2G_EVL_ACHN_LNO_PY_{ts_str}.JPG")
        try:
            img_resp = _get(img_url, referer=page_url)
            frames.append({"data": img_resp.content, "dt": dt})
        except Exception:
            continue
        if len(frames) >= max_hours:
            break
    return frames


# ---------- 区域降雨判断 (基于雷达回波+实时天气) ----------
def analyze_rain(weather):
    """
    综合实时天气与降雨字段, 给出当前降雨状况
    返回: (等级文字, 是否需关注)
    """
    weather_str = (weather.get("weather") or "").lower()
    rain = weather.get("rain")
    rain24 = weather.get("rain24h")

    has_rain_word = any(k in weather_str for k in ["雨", "雪", "rain", "shower"])

    level = "无降雨"
    flag = False
    if rain and rain > 0:
        level = f"当前降雨 {rain}mm"
        flag = True
    elif rain24 and rain24 > 5:
        level = f"近24h降雨 {rain24}mm"
        flag = True
    elif has_rain_word:
        level = weather.get("weather", "有降水")
        flag = True

    return level, flag


def get_city_code(name):
    """按城市名获取代码 (支持别名归一化)"""
    if not name:
        return None
    name = name.strip()
    # 直接匹配
    if name in CITY_FLAT:
        return CITY_FLAT[name]
    # 别名匹配
    std = _ALIASES.get(name)
    if std and std in CITY_FLAT:
        return CITY_FLAT[std]
    # 模糊: 包含匹配 (仅当名称较短且完全包含, 避免误匹配)
    for cn, code in CITY_FLAT.items():
        if name == cn:
            return code
        if len(name) >= 2 and (name in cn):
            if cn.startswith(name) and len(cn) - len(name) <= 2:
                return code
    return None


def get_province_cities(province_name):
    """获取某省份下的城市列表"""
    return PROV_CITIES.get(province_name, [])

def list_provinces():
    """获取所有省份名"""
    return list(PROV_CITIES.keys())
