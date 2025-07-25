#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_forecast.py
CLI：接收参数 lat/lon/date/event/name/out，调用 core_forecast.run_one_forecast 生成 JSON
"""

import argparse
import datetime as dt
import pytz
import json
import os

from core_forecast import run_one_forecast, TZ_DEFAULT

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--date", type=str, required=True, help="YYYY-MM-DD")
    p.add_argument("--event", type=str, choices=["sunrise","sunset"], default="sunrise")
    p.add_argument("--tz", type=str, default=TZ_DEFAULT.zone)
    p.add_argument("--name", type=str, default="自定义地点")
    p.add_argument("--out", type=str, required=True)
    args = p.parse_args()

    tzinfo = pytz.timezone(args.tz)
    date = dt.datetime.strptime(args.date, "%Y-%m-%d").date()

    res = run_one_forecast(args.lat, args.lon, date, args.event, tzinfo, place_name=args.name)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("Written:", args.out)
