#!/usr/bin/env python3
"""Read gpsd JSON stream from stdin, print fix or NO_FIX."""
import sys
import json

min_sats = int(sys.argv[1]) if len(sys.argv) > 1 else 4

lat = lon = alt = None
sats = 0
mode = 0

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    cls = obj.get("class", "")
    if cls == "TPV":
        m = obj.get("mode", 0)
        if m > mode:
            mode = m
        if obj.get("lat") is not None:
            lat = obj["lat"]
        if obj.get("lon") is not None:
            lon = obj["lon"]
        if obj.get("alt") is not None:
            alt = obj["alt"]
    elif cls == "SKY":
        used = sum(1 for sv in obj.get("satellites", []) if sv.get("used"))
        if used > sats:
            sats = used
    if mode >= 2 and lat is not None and lon is not None and sats >= min_sats:
        break

if mode >= 2 and lat is not None and lon is not None and sats >= min_sats:
    dims = "3D" if mode == 3 else "2D"
    print(f"{lat:.6f} {lon:.6f} {sats} {dims}")
else:
    print("NO_FIX")
