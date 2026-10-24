# -*- coding: utf-8 -*-
"""
自动定位模块
两种方式:
  1. 高精度定位 (通过系统定位 API, 需要授权) —— Windows 下可用
  2. IP 定位 (自动, 无需授权, 精度到城市)
返回城市名, 再匹配到城市代码
"""
import json
import re
import requests


def locate_by_ip():
    """通过公网 IP 定位当前城市 (无需授权, 精确到城市)"""
    # 多个免费 IP 定位服务依次尝试
    services = [
        # 太平洋
        {"url": "https://whois.pconline.com.cn/ipJson.jsp?json=true",
         "city_path": ["city"], "prov_path": ["provinces"]},
        # ip-api (国外, 兜底)
        {"url": "http://ip-api.com/json/?lang=zh-CN",
         "city_path": ["city"], "prov_path": ["regionName"]},
    ]
    for svc in services:
        try:
            resp = requests.get(svc["url"], timeout=6,
                                headers={"User-Agent": "Mozilla/5.0",
                                         "Referer": "https://www.baidu.com/"})
            data = resp.json()
            city = _dig(data, svc["city_path"])
            prov = _dig(data, svc["prov_path"])
            if city:
                # 去掉"市/地区/自治区"后缀, 便于匹配城市库
                city = re.sub(r'(市|地区|自治州|盟|区)$', '', str(city))
                prov = re.sub(r'(省|自治区|特别行政区)$', '', str(prov or ''))
                return {"city": city, "province": prov}
        except Exception:
            continue
    return None


def _dig(d, path):
    """按路径取嵌套值"""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def match_city_code(city_name, province_name=None, city_flat=None):
    """
    根据定位结果匹配城市代码 (优先使用 weather_data 的别名/城市库)
    city_flat: {城市名: 代码} (来自 weather_data.CITY_FLAT)
    返回: 城市代码 或 None
    """
    if not city_name:
        return None
    city_name = city_name.strip()
    # 复用 weather_data 的 get_city_code (含别名归一化)
    try:
        from weather_data import get_city_code as wd_get
        code = wd_get(city_name)
        if code:
            return code
    except Exception:
        pass
    # 备用: 直接精确
    if city_flat and city_name in city_flat:
        return city_flat[city_name]
    # 别名 (去后缀)
    norm = re.sub(r'(市|地区|自治州|盟|区|县)$', '', city_name)
    if city_flat and norm in city_flat:
        return city_flat[norm]
    return None
