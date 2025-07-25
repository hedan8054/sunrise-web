#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core_forecast.py
核心逻辑：给定 lat/lon/date/event，生成评分、文案、低云墙风险等结果（字典）。
- 不依赖卫星图；低云墙预警用 open-meteo 多点抽样 + 露点差估算云底高度。
- 可选 meteoblue API（MB_API_KEY 环境变量）增强数据，缺省则自动跳过。

Author: You + ChatGPT
"""

import math
import json
import datetime as dt
from typing import Dict, Any, List, Tuple, Optional

import requests
import pytz
import yaml
import numpy as np
import pandas as pd

# ------------------ 读取配置 ------------------
CONFIG = yaml.safe_load(open("config.yaml", "r", encoding="utf-8"))

TZ_DEFAULT = pytz.timezone(CONFIG.get("timezone", "Asia/Shanghai"))
LAT_DEFAULT = CONFIG["location"]["lat"]
LON_DEFAULT = CONFIG["location"]["lon"]
PLACE_DEFAULT = CONFIG["location"]["name"]
METAR_CODE = CONFIG["location"].get("metar", "ZGSZ")

MB_API_KEY = CONFIG.get("meteoblue", {}).get("api_key", "")  # 也可用 env 覆盖
import os
MB_API_KEY = os.getenv("MB_API_KEY", MB_API_KEY)

# 云底估算常数（m/°C），可在 config.yaml 调整
CB_LAPSE = CONFIG.get("cloudwall", {}).get("cb_lapse_m_per_degC", 125)

# ------------------ 公共工具 ------------------
def to_local(t: dt.datetime, tz: pytz.BaseTzInfo) -> dt.datetime:
    return t.astimezone(tz) if t.tzinfo else tz.localize(t)

def offset_latlon(lat, lon, bearing_deg, dist_km):
    """给定起点(lat,lon)、方位角和距离(km)，返回新的经纬度"""
    R = 6371.0
    brng = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1)*math.cos(dist_km/R) +
                     math.cos(lat1)*math.sin(dist_km/R)*math.cos(brng))
    lon2 = lon1 + math.atan2(math.sin(brng)*math.sin(dist_km/R)*math.cos(lat1),
                             math.cos(lat1)*math.cos(dist_km/R)-math.sin(lat1)*math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)

def get_sun_time(lat, lon, date: dt.date, event="sunrise", tz=TZ_DEFAULT) -> Tuple[dt.datetime, dt.datetime]:
    """
    使用 sunrise-sunset.org 获取指定日期的日出/日落（UTC），转换到 tz。
    返回 (精确时间, 整点时间)
    """
    assert event in ("sunrise", "sunset")
    url = f"https://api.sunrise-sunset.org/json?lat={lat}&lng={lon}&date={date.isoformat()}&formatted=0"
    try:
        js = requests.get(url, timeout=30).json()
        iso = js["results"][event]
        t_aware = dt.datetime.fromisoformat(iso).astimezone(tz)
    except Exception as e:
        print(f"[WARN] sunrise-sunset API 失败({event})：{e}，使用默认时间。")
        default_hour = 6 if event == "sunrise" else 18
        t_aware = tz.localize(dt.datetime.combine(date, dt.time(hour=default_hour, minute=0)))
    t_floor = t_aware.replace(minute=0, second=0, microsecond=0)
    return t_aware, t_floor

def open_meteo(lat, lon, tz="Asia/Shanghai", days=2) -> Optional[dict]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=cloudcover_low,cloudcover_mid,cloudcover_high,visibility,"
        "temperature_2m,dewpoint_2m,windspeed_10m,precipitation"
        f"&forecast_days={days}&timezone={tz.replace('/', '%2F')}"
    )
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "hourly" not in data or "time" not in data["hourly"]:
            print("[ERR] open-meteo hourly 字段缺失：", json.dumps(data)[:200])
            return None
        return data
    except Exception as e:
        print("[ERR] open-meteo 请求失败：", e)
        return None

def metar_text(code=METAR_CODE) -> str:
    try:
        url = f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{code}.TXT"
        txt = requests.get(url, timeout=20).text.strip().splitlines()[-1]
        return txt
    except Exception as e:
        print("[WARN] METAR 获取失败：", e)
        return ""

def parse_cloud_base_from_metar(metar_txt: str) -> Optional[float]:
    import re
    m = re.findall(r'(BKN|OVC)(\d{3})', metar_txt)
    if not m:
        return None
    ft = int(m[0][1]) * 100
    return ft * 0.3048

# ---------- 风险与评分 ----------
def score_value(v, bounds):
    """
    bounds:
      [lo, mid, hi]  -> lo~mid=2分；其它近端=1分；否则0
      [lo, hi]       -> >=hi=2分；>=lo=1分；否则0
    """
    if v is None:
        return 1
    if len(bounds) == 3:
        lo, mid, hi = bounds
        if lo <= v <= mid:
            return 2
        if (mid < v <= hi) or (0 <= v < lo):
            return 1
        return 0
    else:
        lo, hi = bounds
        if v >= hi:
            return 2
        if v >= lo:
            return 1
        return 0

def calc_score(vals: Dict[str, float], cloud_base_m: Optional[float], cfg: dict):
    detail, total = [], 0

    lc = vals["low"]
    pt = score_value(lc, cfg["low_cloud"]); detail.append(("低云%", lc, pt)); total += pt

    mh = max(vals["mid"], vals["high"])
    pt = score_value(mh, cfg["mid_high_cloud"]); detail.append(("中/高云%", mh, pt)); total += pt

    if cloud_base_m is None:
        pt, val = 1, -1
    else:
        lo, hi = cfg["cloud_base_m"]
        pt = 2 if cloud_base_m > hi else 1 if cloud_base_m > lo else 0
        val = cloud_base_m
    detail.append(("云底高度m", val, pt)); total += pt

    vis_km = (vals["vis"] or 0) / 1000.0
    lo, hi = cfg["visibility_km"]
    pt = 2 if vis_km >= hi else 1 if vis_km >= lo else 0
    detail.append(("能见度km", vis_km, pt)); total += pt

    w = vals["wind"]
    lo1, lo2, hi2, hi3 = cfg["wind_ms"]
    if lo2 <= w <= hi2:
        pt = 2
    elif lo1 <= w < lo2 or hi2 < w <= hi3:
        pt = 1
    else:
        pt = 0
    detail.append(("风速m/s", w, pt)); total += pt

    dp = vals["t"] - vals["td"]
    lo, hi = cfg["dewpoint_diff"]
    pt = 2 if dp >= hi else 1 if dp >= lo else 0
    detail.append(("露点差°C", dp, pt)); total += pt

    p = vals.get("precip", 0)
    lo, hi = cfg["precip_mm"]
    pt = 2 if p < lo else 1 if p < hi else 0
    detail.append(("降雨量mm", p, pt)); total += pt

    return total, detail

def gen_scene_desc(score5, kv, event_time, event_name="日出"):
    lc   = kv.get("低云%",      0) or 0
    mh   = kv.get("中/高云%",    0) or 0
    cb   = kv.get("云底高度m",   -1)
    vis  = kv.get("能见度km",    0) or 0
    wind = kv.get("风速m/s",     0) or 0
    dp   = kv.get("露点差°C",    0) or 0
    rp   = kv.get("降雨量mm",    0) or 0

    # 低云
    if lc < 20:   lc_level, low_text = "低",   "地平线基本通透，太阳能“蹦”出来"
    elif lc < 40: lc_level, low_text = "中",   "地平线可能有一条灰带，太阳或从缝隙钻出"
    elif lc < 60: lc_level, low_text = "偏高", "低云偏多，首轮日光可能被挡一部分"
    else:         lc_level, low_text = "高",   "一堵低云墙，首轮日光大概率看不到"

    # 中/高云
    if 20 <= mh <= 60: mh_level, fire_text = "适中",   "有“云接光”舞台，可能染上粉橙色（小概率火烧云）"
    elif mh < 20:      mh_level, fire_text = "太少",   "天空太干净，只有简单渐变色"
    elif mh <= 80:     mh_level, fire_text = "偏多",   "云多且厚，色彩可能偏闷"
    else:              mh_level, fire_text = "很多",   "厚云盖顶，大概率阴沉"

    # 云底
    if cb is None or cb < 0:
        cb_level, cb_text, cb_show = "未知", "云底数据缺失，可参考凌晨或模型", "未知"
    elif cb > 1000:
        cb_level, cb_text, cb_show = ">1000m", "云底较高，多当“天花板”", f"{cb:.0f}m"
    elif cb > 500:
        cb_level, cb_text, cb_show = "500~1000m", "可能在地平线上形成一道云棚", f"{cb:.0f}m"
    else:
        cb_level, cb_text, cb_show = "<500m", "贴地低云/雾，像拉了窗帘", f"{cb:.0f}m"

    if vis >= 15:   vis_level, vis_text = ">15km", "空气透明度好，远景清晰，金光反射漂亮"
    elif vis >= 8:  vis_level, vis_text = "8~15km", "中等透明度，远景略灰"
    else:           vis_level, vis_text = "<8km", "背景灰蒙蒙，层次差"

    if 2 <= wind <= 5: wind_level, wind_text = "2~5m/s", "海面有微波纹，反光好，三脚架稳"
    elif wind < 2:     wind_level, wind_text = "<2m/s",  "几乎无风，注意镜头易结露"
    elif wind <= 8:    wind_level, wind_text = "5~8m/s", "风稍大，留意三脚架稳定性"
    else:              wind_level, wind_text = ">8m/s",  "大风天，拍摄体验差，器材要护好"

    if dp >= 3:     dp_level, dp_text = "≥3℃", "不易起雾"
    elif dp >= 1:   dp_level, dp_text = "1~3℃", "稍潮，镜头可能结露"
    else:           dp_level, dp_text = "<1℃", "极易起雾，注意海雾/镜头起雾风险"

    if rp < 0.1:    rp_level, rain_text = "<0.1mm", "几乎不会下雨"
    elif rp < 1:    rp_level, rain_text = "0.1~1mm", "可能有零星小雨/毛毛雨"
    else:           rp_level, rain_text = "≥1mm", "有下雨可能，注意防水"

    if score5 >= 4.0:   grade = "建议出发（把握较大）"
    elif score5 >= 3.0: grade = "可去一搏（不稳）"
    elif score5 >= 2.0: grade = "机会一般（看心情或距离）"
    elif score5 >= 1.0: grade = "概率很小（除非就在附近）"
    else:               grade = "建议休息（基本无戏）"

    return (
        f"【直观判断】评分：{score5:.1f}/5 —— {grade}\n"
        f"{event_name}：{event_time:%H:%M}\n"
        f"- 低云：{lc:.0f}%（{lc_level}）— {low_text}\n"
        f"- 中/高云：{mh:.0f}%（{mh_level}）— {fire_text}\n"
        f"- 云底高度：{cb_show}（{cb_level}）— {cb_text}\n"
        f"- 能见度：{vis:.1f} km（{vis_level}）— {vis_text}\n"
        f"- 风速：{wind:.1f} m/s（{wind_level}）— {wind_text}\n"
        f"- 降雨：{rp:.1f} mm（{rp_level}）— {rain_text}\n"
        f"- 露点差：{dp:.1f} ℃（{dp_level}）— {dp_text}"
    )

def build_detail_text(total, det, event_time, place_name, event_name="日出"):
    lines = [
        f"拍摄指数：{total}/18",
        f"地点：{place_name}",
        f"{event_name}：{event_time:%H:%M}",
        ""
    ]
    for name, val, pts in det:
        if isinstance(val, float):
            lines.append(f"- {name}: {val:.1f} → {pts}分")
        else:
            lines.append(f"- {name}: {val} → {pts}分")
    return "\n".join(lines)

# ---- 简单风险 + 多点风险 ----
def model_lc_risk_simple(lc, dp, wind):
    if lc is None: return 1
    if lc >= 50 and dp < 2: return 2
    if lc >= 30: return 1
    return 0
RISK_MAP = {0: "正常", 1: "关注", 2: "高风险"}

def mb_point_lowcloud(lat, lon, when_hour) -> Optional[dict]:
    """meteoblue point API（如果有 key）；无则返回 None"""
    if not MB_API_KEY:
        return None
    try:
        url = ("https://my.meteoblue.com/packages/basic-1h_basic-day"
               f"?apikey={MB_API_KEY}&lat={lat:.4f}&lon={lon:.4f}"
               "&format=json&tz=Asia/Shanghai")
        js = requests.get(url, timeout=30).json()
        data = js.get("data_1h") or js.get("data_hourly") or {}
        times = data.get("time") or data.get("time_local") or data.get("time_iso8601") or []
        tgt = when_hour.strftime("%Y-%m-%d %H:00")
        if tgt not in times:
            return None
        idx = times.index(tgt)
        def pick(keys):
            for k in keys:
                if k in data:
                    return data[k][idx]
            return None
        low  = pick(["low_clouds","low_cloud_cover","cloudcover_low"])
        base = pick(["cloud_base","cloudbase","cloud_base_height"])
        return {"low_cloud": low, "cloud_base": base}
    except Exception as e:
        print("[WARN] meteoblue point API 失败：", e)
        return None

def fallback_cloudwall_model(lat, lon, sun_hour, cfg) -> Tuple[int, str, List[Tuple[int,float,Optional[float],str]]]:
    """
    多点采样低云：沿日出方向取多个距离点，优先 meteoblue 拿 low_cloud/connect base，
    否则 open-meteo 兜底，并用 dewpoint 差估算云底高度（CB_LAPSE）。
    返回：risk_score, text, samples(list)
        samples: (dist_km, low_pct, base_m, source_str)
    """
    bearing = cfg.get("sunrise_azimuth", 90)  # 日落时可反向或配置另一个
    dists   = cfg.get("sample_km", [20, 50, 80, 120])

    samples = []
    for d in dists:
        plat, plon = offset_latlon(lat, lon, bearing, d)
        # 先 meteoblue
        rec = mb_point_lowcloud(plat, plon, sun_hour)
        if rec:
            low_pct = rec.get("low_cloud")
            base_m  = rec.get("cloud_base")
            source  = "mb"
        else:
            # open-meteo 兜底
            om = open_meteo(plat, plon)
            if om is None:
                samples.append((d, None, None, "no_data"))
                continue
            times = om["hourly"]["time"]
            tgt = sun_hour.strftime("%Y-%m-%dT%H:00")
            if tgt not in times:
                idx = min(range(len(times)),
                          key=lambda i: abs(dt.datetime.fromisoformat(times[i]) - sun_hour))
            else:
                idx = times.index(tgt)
            low_pct = om["hourly"]["cloudcover_low"][idx]
            # 估算云底：T - Td
            T = om["hourly"]["temperature_2m"][idx]
            Td = om["hourly"]["dewpoint_2m"][idx]
            spread = T - Td
            base_m = spread * CB_LAPSE if spread is not None else None
            source = "om_est"
        samples.append((d, low_pct, base_m, source))

    risk = model_lc_risk_v2(samples)
    txt  = risk_text_from_samples(risk, samples)
    return risk, txt, samples

def model_lc_risk_v2(samples: List[Tuple[int,float,Optional[float],str]]) -> int:
    """
    规则：
      - 任一 low>=50% 且 (cloud_base<600m 或 cloud_base 缺失) => 2
      - 或 >=50% 的样本 low>=30% => 2
      - 若存在样本 low>=30% 或 cloud_base<800m => 1
      - 否则 0
    """
    if not samples:
        return 1
    high = sum(1 for _, l, b, _ in samples if (l is not None and l >= 50) and (b is None or b < 600))
    mid  = sum(1 for _, l, b, _ in samples if (l is not None and l >= 30) or (b is not None and b < 800))
    if high >= 1 or mid >= len(samples) * 0.5:
        return 2
    if mid >= 1:
        return 1
    return 0

def risk_text_from_samples(risk: int, samples: List[Tuple[int,float,Optional[float],str]]) -> str:
    stat = {0:"正常(模型)",1:"关注(模型)",2:"预警(模型)"}.get(risk,"?")
    detail_list = []
    for d, l, b, src in samples:
        ltxt = f"{l:.0f}%" if l is not None else "NA%"
        btxt = f"{int(b)}m" if (b is not None) else "NA m"
        src_tag = {"mb":"mb","om_est":"om","no_data":"none"}.get(src, src)
        detail_list.append(f"{d}km:{ltxt} / {btxt}[{src_tag}]")
    return f"{stat}（samples: " + " | ".join(detail_list) + "）"

# ------------------ 对外主函数 ------------------
def run_one_forecast(lat: float,
                     lon: float,
                     date: dt.date,
                     event: str,
                     tzinfo=TZ_DEFAULT,
                     place_name: str = PLACE_DEFAULT) -> Dict[str, Any]:
    """
    主入口：生成一次（日出/日落）预报结果。
    返回字典，包含：score, score5, detail_text, scene_text, risk_xxx 等。
    """
    assert event in ("sunrise","sunset")

    # 1. 事件时间
    event_exact, event_hour = get_sun_time(lat, lon, date, event=event, tz=tzinfo)

    # 2. open-meteo 主体数据
    om = open_meteo(lat, lon, tz=tzinfo.zone, days=2)
    if om is None:
        raise RuntimeError("open-meteo 数据为空")

    times = om["hourly"]["time"]
    tgt = event_hour.strftime("%Y-%m-%dT%H:00")
    if tgt not in times:
        idx = min(range(len(times)),
                  key=lambda i: abs(dt.datetime.fromisoformat(times[i]) - event_hour))
    else:
        idx = times.index(tgt)

    vals = dict(
        low    = om["hourly"]["cloudcover_low"][idx],
        mid    = om["hourly"]["cloudcover_mid"][idx],
        high   = om["hourly"]["cloudcover_high"][idx],
        vis    = om["hourly"]["visibility"][idx],
        t      = om["hourly"]["temperature_2m"][idx],
        td     = om["hourly"]["dewpoint_2m"][idx],
        wind   = om["hourly"]["windspeed_10m"][idx],
        precip = om["hourly"]["precipitation"][idx]
    )

    # 3. 云底高度：METAR 或估算
    cb_metar = parse_cloud_base_from_metar(metar_text(METAR_CODE))
    if cb_metar is None:
        # 估算
        spread = vals["t"] - vals["td"] if (vals["t"] is not None and vals["td"] is not None) else None
        cb_est = spread * CB_LAPSE if spread is not None else None
        cb_final = cb_est
        cb_reason = "估算"
    else:
        cb_final = cb_metar
        cb_reason = "METAR"

    # 4. 评分
    total, det = calc_score(vals, cb_final, CONFIG["scoring"])
    score5 = round(total / (3 * len(det)) * 5, 1)
    kv = {k: v for k, v, _ in det}

    # 5. 低云墙风险：简单 & 多点
    risk_simple = model_lc_risk_simple(vals["low"], vals["t"] - vals["td"], vals["wind"])
    risk_simple_text = f"{RISK_MAP[risk_simple]}（模型12h）"

    risk_multi, risk_multi_text, samples = fallback_cloudwall_model(lat, lon, event_hour, CONFIG.get("cloudwall", {}))

    # 6. 文案
    scene_txt = (
        gen_scene_desc(score5, kv, event_exact, event_name="日出" if event=="sunrise" else "日落")
        + f"\n- 云底来源：{cb_reason}"
        + f"\n- 低云墙风险（模型12h）：{risk_simple_text}"
        + f"\n- 低云墙预警（模型多点）：{risk_multi_text}"
    )
    detail_txt = build_detail_text(total, det, event_exact, place_name, event_name="日出" if event=="sunrise" else "日落")

    return {
        "meta": {
            "lat": lat, "lon": lon, "place": place_name,
            "date": date.isoformat(),
            "event": event,
            "event_time_local": event_exact.strftime("%Y-%m-%d %H:%M:%S"),
            "tz": tzinfo.zone,
            "generated_at": dt.datetime.now(tzinfo).strftime("%Y-%m-%d %H:%M:%S")
        },
        "inputs": {
            "open_meteo_index_time": om["hourly"]["time"][idx],
            "cb_source": cb_reason,
            "cb_lapse": CB_LAPSE
        },
        "scores": {
            "total18": total,
            "score5": score5,
            "details": det
        },
        "risk": {
            "simple_score": risk_simple,
            "simple_text": risk_simple_text,
            "multi_score": risk_multi,
            "multi_text": risk_multi_text,
            "samples": samples  # list (dist, low, base, source)
        },
        "text": {
            "scene": scene_txt,
            "detail": detail_txt
        },
        "raw": {
            "vals": vals
        }
    }
