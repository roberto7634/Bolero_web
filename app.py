import os
import io
import re
import json
import math
import csv
import zipfile
import datetime
import traceback
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Dict, Any

import requests
import ezdxf
from ezdxf.enums import TextEntityAlignment
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse

try:
    from shapely.geometry import LineString, MultiPolygon, Polygon
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

app = FastAPI(title="Bolero Map — FTTx CAD Studio")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYMBOLS_FILE = os.path.join(BASE_DIR, "symbols.json")

DEFAULT_SYMBOL_LIBRARY = {
    "ODC": {
        "name": "ODC_CABINET",
        "shape": "ODC_Triangle",
        "color_dxf": 1,
        "color_map": "#ef4444",
        "keywords": ["odc", "cabinet", "rk", "feeder"]
    },
    "ODPNEW": {
        "name": "ODP_NEW",
        "shape": "Triangle_Solid",
        "color_dxf": 1,
        "color_map": "#dc2626",
        "keywords": ["odpnew", "odp new", "odp_new", "odp plan", "odp_plan"]
    },
    "ODP": {
        "name": "ODP_BOX",
        "shape": "Triangle_Hollow",
        "color_dxf": 1,
        "color_map": "#f97316",
        "keywords": ["odp", "fat", "distribution"]
    },
    "CLOSURE": {
        "name": "JC_CLOSURE",
        "shape": "Circle_Slash_Out",
        "color_dxf": 4,
        "color_map": "#06b6d4",
        "keywords": ["closure", "jc", "joint", "splice", "suntik"]
    },
    "SLACK": {
        "name": "SLACK_CABLE",
        "shape": "Circle_Slash_In",
        "color_dxf": 5,
        "color_map": "#3b82f6",
        "keywords": ["slack", "coil", "cadangan", "loop"]
    },
    "TE": {
        "name": "TE_POLE",
        "shape": "Circle_Cross",
        "color_dxf": 2,
        "color_map": "#eab308",
        "keywords": ["te", "tiang listrik", "pole_electric"]
    },
    "TN": {
        "name": "TN_POLE",
        "shape": "Solid_Circle",
        "color_dxf": 1,
        "color_map": "#ef4444",
        "keywords": ["tn", "tiang telkom", "pole_telecom"]
    },
    "OLT": {
        "name": "OLT_STATION",
        "shape": "Box_X",
        "color_dxf": 6,
        "color_map": "#d946ef",
        "keywords": ["olt", "central_office", "co", "headend"]
    }
}

INFRA_BOX_ALIASES_FILE = os.path.join(BASE_DIR, "infra_aliases.json")
DEFAULT_INFRA_ALIASES = {
    "MH": ["mh", "manhole"],
    "HH": ["hh", "handhole"],
    "HA": ["ha"],
    "HB": ["hb"]
}


def load_symbols() -> Dict[str, Any]:
    if os.path.exists(SYMBOLS_FILE):
        try:
            with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            pass
    save_symbols(DEFAULT_SYMBOL_LIBRARY)
    return json.loads(json.dumps(DEFAULT_SYMBOL_LIBRARY))


def save_symbols(symbols: Dict[str, Any]):
    with open(SYMBOLS_FILE, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2, ensure_ascii=False)


def load_infra_aliases() -> Dict[str, list]:
    if os.path.exists(INFRA_BOX_ALIASES_FILE):
        try:
            with open(INFRA_BOX_ALIASES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    save_infra_aliases(DEFAULT_INFRA_ALIASES)
    return json.loads(json.dumps(DEFAULT_INFRA_ALIASES))


def save_infra_aliases(aliases: Dict[str, list]):
    with open(INFRA_BOX_ALIASES_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)


def draw_cad_symbol(msp, shape: str, x: float, y: float, s_val: float, layer_name: str, color_dxf: int = 7):
    r3 = math.sqrt(3.0)

    if shape == "ODC_Triangle":
        R_out = 2.1 * s_val
        R_in = 1.35 * s_val
        v_out = [
            (x, y + R_out),
            (x + R_out * r3 / 2.0, y - R_out / 2.0),
            (x - R_out * r3 / 2.0, y - R_out / 2.0)
        ]
        v_in = [
            (x, y + R_in),
            (x + R_in * r3 / 2.0, y - R_in / 2.0),
            (x - R_in * r3 / 2.0, y - R_in / 2.0)
        ]
        msp.add_lwpolyline(v_out + [v_out[0]], dxfattribs={'layer': layer_name, 'flags': 1})
        msp.add_lwpolyline(v_in + [v_in[0]], dxfattribs={'layer': layer_name, 'flags': 1})
        for i in range(3):
            p_vertex = v_out[i]
            p_opp1 = v_out[(i + 1) % 3]
            p_opp2 = v_out[(i + 2) % 3]
            p_mid = ((p_opp1[0] + p_opp2[0]) / 2.0, (p_opp1[1] + p_opp2[1]) / 2.0)
            msp.add_line(p_vertex, p_mid, dxfattribs={'layer': layer_name})

    elif shape == "Triangle_Hollow":
        R = 1.8 * s_val
        pts = [
            (x, y + R),
            (x + R * r3 / 2.0, y - R / 2.0),
            (x - R * r3 / 2.0, y - R / 2.0)
        ]
        msp.add_lwpolyline(pts + [pts[0]], dxfattribs={'layer': layer_name, 'flags': 1})

    elif shape == "Triangle_Solid":
        R = 1.8 * s_val
        pts = [
            (x, y + R),
            (x + R * r3 / 2.0, y - R / 2.0),
            (x - R * r3 / 2.0, y - R / 2.0)
        ]
        msp.add_lwpolyline(pts + [pts[0]], dxfattribs={'layer': layer_name, 'flags': 1})
        hatch = msp.add_hatch(color=color_dxf, dxfattribs={'layer': layer_name})
        hatch.set_solid_fill(color=color_dxf)
        hatch.paths.add_polyline_path(pts, is_closed=True)

    elif shape == "Circle_Slash_Out":
        rad = 1.35 * s_val
        ext = 1.95 * s_val
        d = ext / math.sqrt(2.0)
        msp.add_circle((x, y), radius=rad, dxfattribs={'layer': layer_name})
        msp.add_line((x - d, y - d), (x + d, y + d), dxfattribs={'layer': layer_name})

    elif shape == "Circle_Slash_In":
        rad = 1.35 * s_val
        d = rad / math.sqrt(2.0)
        msp.add_circle((x, y), radius=rad, dxfattribs={'layer': layer_name})
        msp.add_line((x - d, y - d), (x + d, y + d), dxfattribs={'layer': layer_name})

    elif shape == "Circle_Cross":
        msp.add_circle((x, y), radius=1.2 * s_val, dxfattribs={'layer': layer_name})
        msp.add_line((x - 1.5 * s_val, y), (x + 1.5 * s_val, y), dxfattribs={'layer': layer_name})
        msp.add_line((x, y - 1.5 * s_val), (x, y + 1.5 * s_val), dxfattribs={'layer': layer_name})

    elif shape == "Solid_Circle":
        for r in [0.3 * s_val, 0.6 * s_val, 0.9 * s_val, 1.2 * s_val]:
            msp.add_circle((x, y), radius=r, dxfattribs={'layer': layer_name})

    elif shape == "Box_X":
        pts = [
            (x - 1.5 * s_val, y - 1.5 * s_val),
            (x + 1.5 * s_val, y - 1.5 * s_val),
            (x + 1.5 * s_val, y + 1.5 * s_val),
            (x - 1.5 * s_val, y + 1.5 * s_val)
        ]
        msp.add_lwpolyline(pts + [pts[0]], dxfattribs={'layer': layer_name, 'flags': 1})
        msp.add_line(pts[0], pts[2], dxfattribs={'layer': layer_name})
        msp.add_line(pts[3], pts[1], dxfattribs={'layer': layer_name})

    elif shape == "Diamond":
        pts = [
            (x, y + 1.6 * s_val),
            (x + 1.6 * s_val, y),
            (x, y - 1.6 * s_val),
            (x - 1.6 * s_val, y)
        ]
        msp.add_lwpolyline(pts + [pts[0]], dxfattribs={'layer': layer_name, 'flags': 1})

    else:
        msp.add_circle((x, y), radius=1.0 * s_val, dxfattribs={'layer': layer_name})


def calculate_utm_zone(lon: float) -> int:
    return int((lon + 180) / 6) + 1


def latlon_to_utm_xy(lat: float, lon: float, zone: int):
    a = 6378137.0
    f = 1 / 298.257223563
    e2 = 2 * f - f * f
    e_prime2 = e2 / (1 - e2)
    k0 = 0.9996

    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0_rad = math.radians((zone - 1) * 6 - 180 + 3)

    N = a / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    T = math.tan(lat_rad) ** 2
    C = e_prime2 * math.cos(lat_rad) ** 2
    A = math.cos(lat_rad) * (lon_rad - lon0_rad)

    M = a * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * lat_rad)
    )

    x = k0 * N * (
        A + (1 - T + C) * A ** 3 / 6
        + (5 - 18 * T + T ** 2 + 72 * C - 58 * e_prime2) * A ** 5 / 120
    ) + 500000.0

    y = k0 * (
        M + N * math.tan(lat_rad) * (
            A ** 2 / 2 + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
            + (61 - 58 * T + T ** 2 + 600 * C - 330 * e_prime2) * A ** 6 / 720
        )
    )
    if lat < 0:
        y += 10000000.0
    return x, y


def get_point_category(name: str, infra_aliases: Dict[str, list] = None):
    upper_name = name.upper()
    for cat in ('MH', 'HH', 'HA', 'HB'):
        if cat in upper_name:
            return cat
    if infra_aliases:
        lower_name = name.lower()
        for cat in ('MH', 'HH', 'HA', 'HB'):
            for alias in infra_aliases.get(cat, []):
                if alias.strip().lower() and alias.strip().lower() in lower_name:
                    return cat
    return None


def match_symbol_type(name: str, symbol_lib: Dict[str, Any], infra_aliases: Dict[str, list] = None) -> str:
    cat_special = get_point_category(name, infra_aliases)
    if cat_special:
        return cat_special

    name_lower = name.lower()
    candidates = []
    for sym_key, sym_val in symbol_lib.items():
        for kw in sym_val.get("keywords", []):
            kw_clean = kw.strip().lower()
            if kw_clean:
                candidates.append((len(kw_clean), kw_clean, sym_key))
    candidates.sort(key=lambda item: item[0], reverse=True)

    for _, kw_clean, sym_key in candidates:
        if kw_clean in name_lower:
            return sym_key
    return "DEFAULT"


def classify_kml_route(raw_name: str):
    text = raw_name.lower().strip()
    if any(k in text for k in ('galian', 'boring', 'hdpe', 'duct', 'trench')):
        return 'GALIAN', 30, "#F97316"
    if 'cl' in text:
        if '144' in text:
            return 'CL_144', 1, "#DC2626"
        if '96' in text:
            return 'CL_96', 1, "#DC2626"
        if '48' in text:
            return 'CL_48', 1, "#DC2626"
        if '24' in text:
            return 'CL_24', 1, "#DC2626"
        return 'CL_KABEL', 1, "#DC2626"
    if '96' in text:
        return 'ADSS_96', 1, "#DC2626"
    if '48' in text:
        return 'ADSS_48', 5, "#2563EB"
    if any(k in text for k in ('12.24', '12/24', '12-24', '12', '24')):
        return 'ADSS_12_24', 5, "#2563EB"

    clean_name = "".join(c if c.isalnum() or c in ('_', '-', ' ') else '_' for c in raw_name).strip()
    if not clean_name or clean_name.lower() == "penanda":
        clean_name = "JALUR_KABEL_KML"
    return clean_name, 7, "#16A34A"


def parse_kml_bytes(file_bytes: bytes, filename: str):
    symbol_lib = load_symbols()
    infra_aliases = load_infra_aliases()

    if filename.lower().endswith(".kmz"):
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            kml_files = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_files:
                raise ValueError("Tidak ditemukan file .kml di dalam arsip .kmz")
            xml_content = zf.read(kml_files[0])
    else:
        xml_content = file_bytes

    root = ET.fromstring(xml_content)

    def get_clean_tag(node):
        return node.tag.split('}')[-1]

    def find_nodes_by_tag_suffix(node, suffix):
        results = []
        if get_clean_tag(node) == suffix:
            results.append(node)
        for child in node:
            results.extend(find_nodes_by_tag_suffix(child, suffix))
        return results

    points = []
    paths = []
    all_lats = []
    all_lons = []

    placemarks = find_nodes_by_tag_suffix(root, 'Placemark')
    for pm in placemarks:
        name_elem = find_nodes_by_tag_suffix(pm, 'name')
        name = name_elem[0].text.strip() if name_elem and name_elem[0].text else "Penanda"

        point_nodes = find_nodes_by_tag_suffix(pm, 'Point')
        if point_nodes:
            coord_nodes = find_nodes_by_tag_suffix(point_nodes[0], 'coordinates')
            if coord_nodes and coord_nodes[0].text:
                parts = coord_nodes[0].text.strip().split(',')
                if len(parts) >= 2:
                    try:
                        lon = float(parts[0].strip())
                        lat = float(parts[1].strip())
                        detected_type = match_symbol_type(name, symbol_lib, infra_aliases)
                        points.append({
                            "lat": lat,
                            "lon": lon,
                            "name": name,
                            "type": detected_type
                        })
                        all_lats.append(lat)
                        all_lons.append(lon)
                    except ValueError:
                        pass

        line_nodes = find_nodes_by_tag_suffix(pm, 'LineString') or find_nodes_by_tag_suffix(pm, 'Polygon')
        if line_nodes:
            coord_nodes = find_nodes_by_tag_suffix(line_nodes[0], 'coordinates')
            if coord_nodes and coord_nodes[0].text:
                coord_strings = coord_nodes[0].text.strip().split()
                path_points = []
                for c_str in coord_strings:
                    parts = c_str.split(',')
                    if len(parts) >= 2:
                        try:
                            lon = float(parts[0].strip())
                            lat = float(parts[1].strip())
                            path_points.append([lat, lon])
                            all_lats.append(lat)
                            all_lons.append(lon)
                        except ValueError:
                            pass
                if len(path_points) > 1:
                    layer_cad, dxf_color, map_color = classify_kml_route(name)
                    paths.append({
                        "name": layer_cad,
                        "raw_name": name,
                        "dxf_color": dxf_color,
                        "map_color": map_color,
                        "coords": path_points
                    })

    if not all_lats:
        raise ValueError("Tidak ditemukan koordinat valid di dalam berkas KML/KMZ.")

    zone_num = calculate_utm_zone(sum(all_lons) / len(all_lons))

    total_meters = 0.0
    for path in paths:
        pts_utm = [latlon_to_utm_xy(lat, lon, zone_num) for lat, lon in path["coords"]]
        seg_m = sum(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) for p1, p2 in zip(pts_utm, pts_utm[1:]))
        path["length_m"] = round(seg_m, 2)
        total_meters += seg_m

    counts = {
        "MANHOLE": 0,
        "HANDHOLE": 0,
        "CLOSURE": 0,
        "ODP": 0,
        "ODC": 0,
        "SLACK": 0,
        "TIANG": 0,
        "SEGMEN_KABEL": len(paths),
        "LAINNYA": 0
    }
    for pt in points:
        t = pt["type"]
        if t == "MH":
            counts["MANHOLE"] += 1
        elif t in ("HH", "HA", "HB"):
            counts["HANDHOLE"] += 1
        elif t in ("CLOSURE", "JC"):
            counts["CLOSURE"] += 1
        elif t in ("ODP", "ODPNEW"):
            counts["ODP"] += 1
        elif t == "ODC":
            counts["ODC"] += 1
        elif t == "SLACK":
            counts["SLACK"] += 1
        elif t in ("TN", "TE"):
            counts["TIANG"] += 1
        else:
            counts["LAINNYA"] += 1

    bounds = {
        "north": max(all_lats) + 0.0008,
        "south": min(all_lats) - 0.0008,
        "east": max(all_lons) + 0.0012,
        "west": min(all_lons) - 0.0012,
    }

    return {
        "filename": filename,
        "points": points,
        "paths": paths,
        "bounds": bounds,
        "summary": {
            "total_fitur": len(points) + len(paths),
            "total_panjang_km": round(total_meters / 1000.0, 2),
            "counts": counts
        }
    }


def snap_to_road_position(x, y, road_segments, category, sym_factor=1.0):
    best_dist_sq = float('inf')
    best_proj = (x, y)
    best_angle = 0.0
    best_half_width = 3.0
    best_side = 1.0

    for (x1, y1), (x2, y2), hw in road_segments:
        dx = x2 - x1
        dy = y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            continue
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg_len_sq))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        dist_sq = (x - proj_x) ** 2 + (y - proj_y) ** 2

        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_proj = (proj_x, proj_y)
            best_angle = math.atan2(dy, dx)
            best_half_width = hw
            cross = dx * (y - y1) - dy * (x - x1)
            best_side = 1.0 if cross >= 0 else -1.0

    if best_dist_sq > 1600.0 or best_dist_sq == float('inf'):
        return x, y, best_angle

    if category == 'MH':
        # MH masuk ke dalam badan jalan sejauh setengah lebar kotak + margin 0.25m dari tepi jalan
        target_offset = max(0.5, best_half_width - (1.20 * sym_factor) - 0.25)
    else:
        # HH berada di luar garis tepi jalan sejauh setengah lebar kotak + margin 0.25m
        target_offset = best_half_width + (1.10 * sym_factor) + 0.25

    nx = -math.sin(best_angle) * best_side
    ny = math.cos(best_angle) * best_side
    return best_proj[0] + nx * target_offset, best_proj[1] + ny * target_offset, best_angle


def draw_dxf_legend(doc, msp, bbox_tracker, counter, symbol_lib, scale_label="1:1000"):
    if bbox_tracker['min_x'] >= bbox_tracker['max_x'] or bbox_tracker['min_y'] >= bbox_tracker['max_y']:
        return
    if 'LEGENDA' not in doc.layers:
        doc.layers.new(name='LEGENDA', dxfattribs={'color': 7})

    box_w, row_h, header_h = 52.0, 5.5, 8.0
    legend_items = []

    for sym_key, cfg in symbol_lib.items():
        qty = counter.get(cfg["name"], 0)
        legend_items.append(('SYMBOL', cfg["shape"], f"{sym_key} - {cfg['name']} ({qty} unit)", cfg["color_dxf"]))

    legend_items.extend([
        ('BOX_MH', None, f"MH - Manhole ({counter.get('MH', 0)} unit)", 7),
        ('BOX_HH', None, f"HH / HA / HB ({counter.get('HH', 0) + counter.get('HA', 0) + counter.get('HB', 0)} unit)", 7),
        ('LINE', None, "Kabel ADSS 96 (Merah)", 1),
        ('LINE', None, "Kabel ADSS 48 / 12.24 (Biru)", 5),
        ('LINE', None, "Kabel CL 24/48/96/144 (Merah)", 1),
        ('LINE', None, "Jalur Galian / HDPE (Oren)", 30),
    ])

    box_h = header_h + len(legend_items) * row_h + 4.0
    x0 = bbox_tracker['max_x'] + 12.0
    y0 = bbox_tracker['min_y']
    x1 = x0 + box_w
    y1 = y0 + box_h

    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], dxfattribs={'layer': 'LEGENDA', 'color': 7, 'flags': 1})
    msp.add_line((x0, y1 - header_h), (x1, y1 - header_h), dxfattribs={'layer': 'LEGENDA', 'color': 7})
    msp.add_text(
        f"LEGENDA JARINGAN (SKALA {scale_label})",
        dxfattribs={'layer': 'LEGENDA', 'height': 1.6, 'color': 2}
    ).set_placement(((x0 + x1) / 2.0, y1 - header_h / 2.0), align=TextEntityAlignment.MIDDLE_CENTER)

    sym_x, txt_x = x0 + 6.5, x0 + 13.5
    for idx, (kind, shape_name, label, aci_color) in enumerate(legend_items):
        cy = y1 - header_h - 3.5 - idx * row_h
        if kind == 'SYMBOL':
            draw_cad_symbol(msp, shape_name, sym_x, cy, 0.75, 'LEGENDA', color_dxf=aci_color)
        elif kind == 'BOX_MH':
            msp.add_lwpolyline([(sym_x - 1.4, cy - 0.85), (sym_x + 1.4, cy - 0.85), (sym_x + 1.4, cy + 0.85), (sym_x - 1.4, cy + 0.85), (sym_x - 1.4, cy - 0.85)], dxfattribs={'layer': 'LEGENDA', 'color': 7, 'flags': 1})
            msp.add_lwpolyline([(sym_x - 1.1, cy - 0.55), (sym_x + 1.1, cy - 0.55), (sym_x + 1.1, cy + 0.55), (sym_x - 1.1, cy + 0.55), (sym_x - 1.1, cy - 0.55)], dxfattribs={'layer': 'LEGENDA', 'color': 7, 'flags': 1})
        elif kind == 'BOX_HH':
            msp.add_lwpolyline([(sym_x - 0.8, cy - 0.8), (sym_x + 0.8, cy - 0.8), (sym_x + 0.8, cy + 0.8), (sym_x - 0.8, cy + 0.8), (sym_x - 0.8, cy - 0.8)], dxfattribs={'layer': 'LEGENDA', 'color': 7, 'flags': 1})
            msp.add_lwpolyline([(sym_x - 0.575, cy - 0.575), (sym_x + 0.575, cy - 0.575), (sym_x + 0.575, cy + 0.575), (sym_x - 0.575, cy + 0.575), (sym_x - 0.575, cy - 0.575)], dxfattribs={'layer': 'LEGENDA', 'color': 7, 'flags': 1})
        elif kind == 'LINE':
            msp.add_line((sym_x - 3.5, cy), (sym_x + 3.5, cy), dxfattribs={'layer': 'LEGENDA', 'color': aci_color})

        msp.add_text(label, dxfattribs={'layer': 'LEGENDA', 'height': 1.25, 'color': 7}).set_placement((txt_x, cy), align=TextEntityAlignment.MIDDLE_LEFT)

    bbox_tracker['max_x'] = max(bbox_tracker['max_x'], x1)
    bbox_tracker['max_y'] = max(bbox_tracker['max_y'], y1)
    bbox_tracker['min_y'] = min(bbox_tracker['min_y'], y0)


def generate_dxf_and_boq(payload: Dict[str, Any]):
    symbol_lib = load_symbols()
    infra_aliases = load_infra_aliases()
    bounds = payload["bounds"]
    n, s, e, w = float(bounds["north"]), float(bounds["south"]), float(bounds["east"]), float(bounds["west"])
    scale_label = payload.get("scale", "1:1000")
    text_size = payload.get("text_size", "Normal")
    layers_opt = payload.get("layers", {"jalan": True, "bangunan": True, "air": True, "teks": True})
    kml_points = payload.get("points", [])
    kml_paths = payload.get("paths", [])

    scale_map = {"1:100": 0.55, "1:250": 0.70, "1:500": 0.85, "1:1000": 1.00, "1:2000": 1.35}
    text_map = {"Kecil": 0.80, "Normal": 1.00, "Besar": 1.30}
    sym_factor = scale_map.get(scale_label, 1.0)
    txt_factor = sym_factor * text_map.get(text_size, 1.0)

    bbox_str = f"{s},{w},{n},{e}"
    layer_queries = []
    if layers_opt.get("jalan", True):
        layer_queries.append(f'way["highway"]({bbox_str});')
    if layers_opt.get("bangunan", True):
        layer_queries.append(f'way["building"]({bbox_str});')
    if layers_opt.get("air", True):
        layer_queries.append(f'way["waterway"]({bbox_str});way["natural"="water"]({bbox_str});')

    elements = []
    if layer_queries:
        overpass_query = f'[out:json][timeout:60];({" ".join(layer_queries)});out geom;'
        servers = [
            "https://overpass.kumi.systems/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.openstreetmap.fr/api/interpreter",
            "https://overpass-api.de/api/interpreter"
        ]
        for srv in servers:
            try:
                r = requests.post(srv, data={"data": overpass_query}, headers={"User-Agent": "BoleroMapWeb/1.0"}, timeout=30)
                if r.status_code == 200:
                    elements = r.json().get("elements", [])
                    break
            except Exception:
                continue

    center_lon = (e + w) / 2.0
    zone_num = calculate_utm_zone(center_lon)

    def to_xy(lon_val: float, lat_val: float):
        return latlon_to_utm_xy(lat_val, lon_val, zone_num)

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    doc.layers.new(name='JALAN', dxfattribs={'color': 7})
    doc.layers.new(name='BANGUNAN', dxfattribs={'color': 1})
    doc.layers.new(name='SUNGAI', dxfattribs={'color': 5})
    doc.layers.new(name='TEKS_NAMA', dxfattribs={'color': 2})

    counter = {"JALAN": 0, "BANGUNAN": 0, "SUNGAI": 0, "TEKS_NAMA": 0}
    for val in symbol_lib.values():
        l_name = val["name"]
        if l_name not in doc.layers:
            doc.layers.new(name=l_name, dxfattribs={'color': val["color_dxf"]})
        counter[l_name] = 0

    bbox_tracker = {'min_x': float('inf'), 'min_y': float('inf'), 'max_x': float('-inf'), 'max_y': float('-inf')}

    def track_coord(px, py):
        if px < bbox_tracker['min_x']: bbox_tracker['min_x'] = px
        if py < bbox_tracker['min_y']: bbox_tracker['min_y'] = py
        if px > bbox_tracker['max_x']: bbox_tracker['max_x'] = px
        if py > bbox_tracker['max_y']: bbox_tracker['max_y'] = py

    allowed_roads = [
        'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link',
        'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street'
    ]
    alignment_segments = []
    for elem in elements:
        geom = elem.get('geometry', [])
        tags = elem.get('tags', {})
        rt = tags.get('highway')
        if rt in allowed_roads and len(geom) >= 2:
            rw = 12.0 if rt in ['motorway', 'trunk', 'primary'] else (8.0 if rt in ['secondary', 'tertiary'] else (6.0 if rt in ['residential', 'living_street'] else 4.0))
            pts_road = [to_xy(p['lon'], p['lat']) for p in geom]
            for p1, p2 in zip(pts_road, pts_road[1:]):
                alignment_segments.append((p1, p2, rw / 2.0))

    # 1. Simbol Titik (MH Besar + 2 Garis Tutup, HH Diperbesar)
    point_coordinates = {"MH": [], "HH": [], "HA": [], "HB": []}
    for pt in kml_points:
        x, y = to_xy(pt['lon'], pt['lat'])
        sym_type = match_symbol_type(pt['name'], symbol_lib, infra_aliases)
        pt_cat = get_point_category(pt['name'], infra_aliases)
        sep_cat = pt_cat if pt_cat in {'MH', 'HH', 'HA', 'HB'} else None
        pt_layer = sep_cat or 'TEKS_NAMA'

        if sep_cat:
            if sep_cat not in doc.layers:
                doc.layers.new(name=sep_cat, dxfattribs={'color': 7})
            dist_layer = f"JARAK_{sep_cat}"
            if dist_layer not in doc.layers:
                doc.layers.new(name=dist_layer, dxfattribs={'color': 2})
            counter.setdefault(sep_cat, 0)
            counter.setdefault(dist_layer, 0)

        if sep_cat in {'MH', 'HH', 'HA', 'HB'}:
            if sep_cat == 'MH':
                # Ukuran MH besar untuk cetak (total 4.40 m x 2.40 m pada skala 1:1000)
                outer_hw, outer_hh = 2.20 * sym_factor, 1.20 * sym_factor
                inner_hw, inner_hh = 1.80 * sym_factor, 0.90 * sym_factor
            else:
                # Ukuran HH / HA / HB diperbesar (total 2.20 m x 2.20 m pada skala 1:1000)
                outer_hw, outer_hh = 1.10 * sym_factor, 1.10 * sym_factor
                inner_hw, inner_hh = 0.82 * sym_factor, 0.82 * sym_factor

            x, y, angle = snap_to_road_position(x, y, alignment_segments, sep_cat, sym_factor)
            cos_a, sin_a = math.cos(angle), math.sin(angle)

            def rot_pt(lx, ly):
                return (x + lx * cos_a - ly * sin_a, y + lx * sin_a + ly * cos_a)

            outer_pts = [rot_pt(-outer_hw, -outer_hh), rot_pt(outer_hw, -outer_hh), rot_pt(outer_hw, outer_hh), rot_pt(-outer_hw, outer_hh), rot_pt(-outer_hw, -outer_hh)]
            inner_pts = [rot_pt(-inner_hw, -inner_hh), rot_pt(inner_hw, -inner_hh), rot_pt(inner_hw, inner_hh), rot_pt(-inner_hw, inner_hh), rot_pt(-inner_hw, -inner_hh)]

            msp.add_lwpolyline(outer_pts, dxfattribs={'layer': sep_cat, 'color': 7, 'flags': 1})
            msp.add_lwpolyline(inner_pts, dxfattribs={'layer': sep_cat, 'color': 7, 'flags': 1})

            # Gambar 2 garis pembagi tutup (3 petak) di dalam MH
            if sep_cat == 'MH':
                div1_x = -inner_hw + (2.0 * inner_hw / 3.0)
                div2_x = -inner_hw + (4.0 * inner_hw / 3.0)
                msp.add_line(rot_pt(div1_x, -inner_hh), rot_pt(div1_x, inner_hh), dxfattribs={'layer': sep_cat, 'color': 7})
                msp.add_line(rot_pt(div2_x, -inner_hh), rot_pt(div2_x, inner_hh), dxfattribs={'layer': sep_cat, 'color': 7})

            counter[sep_cat] += 1
            point_coordinates[sep_cat].append((x, y))
        elif sym_type not in symbol_lib:
            if 'TITIK_KML' not in doc.layers:
                doc.layers.new(name='TITIK_KML', dxfattribs={'color': 6})
            counter.setdefault('TITIK_KML', 0)
            msp.add_circle((x, y), radius=0.8 * sym_factor, dxfattribs={'layer': 'TITIK_KML'})
            counter['TITIK_KML'] += 1
        else:
            cfg = symbol_lib[sym_type]
            l_name = cfg["name"]
            draw_cad_symbol(msp, cfg["shape"], x, y, sym_factor, l_name, color_dxf=cfg["color_dxf"])
            counter[l_name] = counter.get(l_name, 0) + 1

        track_coord(x, y)
        if pt_cat:
            offset_x = (2.45 if sep_cat == 'MH' else 1.35) * sym_factor
            offset_y = (1.35 if sep_cat == 'MH' else 1.25) * sym_factor
            msp.add_text(pt['name'], dxfattribs={'layer': pt_layer, 'height': 0.65 * txt_factor, 'color': 7}).set_placement((x + offset_x, y + offset_y))
        else:
            msp.add_text(pt['name'], dxfattribs={'layer': pt_layer, 'height': 0.95 * txt_factor}).set_placement((x + 1.8 * sym_factor, y + 0.4 * sym_factor))

    for cat, coords in point_coordinates.items():
        if len(coords) >= 2:
            dist_layer = f"JARAK_{cat}"
            for p1, p2 in zip(coords, coords[1:]):
                dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                msp.add_text(f"{dist:.2f} m", dxfattribs={'layer': dist_layer, 'height': 0.9 * txt_factor}).set_placement(((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0), align=TextEntityAlignment.MIDDLE_CENTER)

    # 2. Jalur Kabel & Galian
    cable_records = []
    for path in kml_paths:
        c_layer = path['name']
        raw_lbl = path['raw_name']
        d_col = path.get('dxf_color', 7)
        if c_layer not in doc.layers:
            doc.layers.new(name=c_layer, dxfattribs={'color': d_col})
        pts_dxf = [to_xy(lon, lat) for lat, lon in path['coords']]
        for px, py in pts_dxf:
            track_coord(px, py)
        if len(pts_dxf) > 1:
            msp.add_lwpolyline(pts_dxf, dxfattribs={'layer': c_layer})
            seg_len = sum(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) for p1, p2 in zip(pts_dxf, pts_dxf[1:]))
            cable_records.append({"raw_name": raw_lbl, "layer": c_layer, "length_m": seg_len})

    # 3. Peta Jalan & Bangunan OSM
    road_polygons = []
    placed_labels = {}
    for elem in elements:
        geom = elem.get('geometry', [])
        if len(geom) < 2:
            continue
        tags = elem.get('tags', {})
        pts_dxf = [to_xy(p['lon'], p['lat']) for p in geom]
        for px, py in pts_dxf:
            track_coord(px, py)

        if 'highway' in tags and layers_opt.get("jalan", True):
            rt = tags.get('highway')
            if rt in allowed_roads:
                rw = 12.0 if rt in ['motorway', 'trunk', 'primary'] else (8.0 if rt in ['secondary', 'tertiary'] else (6.0 if rt in ['residential', 'living_street'] else 4.0))
                if HAS_SHAPELY:
                    try:
                        buf = LineString(pts_dxf).buffer(rw / 2.0, cap_style='round', join_style='round')
                        if buf.is_valid and not buf.is_empty:
                            road_polygons.append(buf)
                    except Exception:
                        pass
                if layers_opt.get("teks", True) and 'name' in tags:
                    nm = tags.get('name', '').strip()
                    slen = sum(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) for p1, p2 in zip(pts_dxf, pts_dxf[1:]))
                    if nm and slen >= 25.0:
                        mid = pts_dxf[len(pts_dxf) // 2]
                        if not any(math.hypot(mid[0] - px, mid[1] - py) < 120.0 for px, py in placed_labels.get(nm, [])):
                            pa, pb = pts_dxf[max(0, len(pts_dxf) // 2 - 1)], pts_dxf[min(len(pts_dxf) - 1, len(pts_dxf) // 2)]
                            ang = math.degrees(math.atan2(pb[1] - pa[1], pb[0] - pa[0]))
                            if ang > 90 or ang < -90:
                                ang += 180
                            msp.add_text(nm, dxfattribs={'layer': 'TEKS_NAMA', 'height': 1.6 * txt_factor, 'rotation': ang}).set_placement(mid, align=TextEntityAlignment.MIDDLE_CENTER)
                            placed_labels.setdefault(nm, []).append(mid)
        elif 'building' in tags and layers_opt.get("bangunan", True):
            msp.add_lwpolyline(pts_dxf, dxfattribs={'layer': 'BANGUNAN', 'flags': 1})
        elif ('waterway' in tags or tags.get('natural') == 'water') and layers_opt.get("air", True):
            msp.add_lwpolyline(pts_dxf, dxfattribs={'layer': 'SUNGAI'})

    if HAS_SHAPELY and road_polygons:
        try:
            merged = unary_union(road_polygons)
            merged = merged.buffer(2.5, cap_style='round', join_style='round').buffer(-2.5, cap_style='round', join_style='round')
            merged = merged.buffer(-2.0, join_style='round').buffer(2.0, join_style='round').simplify(0.25, preserve_topology=True)
            polys = [merged] if isinstance(merged, Polygon) else (list(merged.geoms) if isinstance(merged, MultiPolygon) else [])
            for poly in polys:
                if poly.area >= 30.0:
                    msp.add_lwpolyline(list(poly.exterior.coords), dxfattribs={'layer': 'JALAN', 'flags': 1})
                    for interior in poly.interiors:
                        if Polygon(interior).area >= 250.0:
                            msp.add_lwpolyline(list(interior.coords), dxfattribs={'layer': 'JALAN', 'flags': 1})
        except Exception:
            for poly in road_polygons:
                if isinstance(poly, Polygon) and poly.area >= 30.0:
                    msp.add_lwpolyline(list(poly.exterior.coords), dxfattribs={'layer': 'JALAN', 'flags': 1})

    # 4. Kotak Legenda
    draw_dxf_legend(doc, msp, bbox_tracker, counter, symbol_lib, scale_label=scale_label)

    # 5. Auto-Zoom Viewport
    if bbox_tracker['min_x'] < bbox_tracker['max_x'] and bbox_tracker['min_y'] < bbox_tracker['max_y']:
        xmin, ymin = bbox_tracker['min_x'] - 15.0, bbox_tracker['min_y'] - 15.0
        xmax, ymax = bbox_tracker['max_x'] + 15.0, bbox_tracker['max_y'] + 15.0
        doc.header['$EXTMIN'] = (xmin, ymin, 0.0)
        doc.header['$EXTMAX'] = (xmax, ymax, 0.0)
        vps = doc.viewports.get('*Active')
        vp = vps[0] if isinstance(vps, list) and vps else vps
        if vp and hasattr(vp, 'dxf'):
            vp.dxf.center = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
            vp.dxf.height = max(ymax - ymin, (xmax - xmin) * 0.65, 20.0)

    dxf_stream = io.StringIO()
    doc.write(dxf_stream)
    dxf_bytes = dxf_stream.getvalue().encode('utf-8')

    # Buat CSV Rekap BOQ
    csv_stream = io.StringIO()
    writer = csv.writer(csv_stream, delimiter=';')
    writer.writerow(["REKAPITULASI BOQ JARINGAN — BOLERO MAP"])
    writer.writerow(["Tanggal Ekspor", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])
    writer.writerow(["1. REKAP TOTAL PANJANG KABEL & GALIAN"])
    writer.writerow(["No", "Jenis / Layer Kabel", "Jumlah Segmen", "Total Panjang (m)"])
    summary_cables = {}
    for rec in cable_records:
        summary_cables.setdefault(rec['layer'], {'count': 0, 'total_m': 0.0})
        summary_cables[rec['layer']]['count'] += 1
        summary_cables[rec['layer']]['total_m'] += rec['length_m']
    for idx, (lname, info) in enumerate(sorted(summary_cables.items()), start=1):
        writer.writerow([idx, lname, info['count'], f"{info['total_m']:.2f}".replace('.', ',')])

    writer.writerow([])
    writer.writerow(["2. REKAP JUMLAH KOMPONEN INFRASTRUKTUR"])
    writer.writerow(["No", "Kategori / Layer Aset", "Jumlah (Unit)"])
    asset_list = [(f"{k} ({v['name']})", counter.get(v['name'], 0)) for k, v in symbol_lib.items()]
    asset_list.extend([
        ("Manhole (MH)", counter.get("MH", 0)),
        ("Handhole (HH)", counter.get("HH", 0)),
        ("Handhole (HA)", counter.get("HA", 0)),
        ("Handhole (HB)", counter.get("HB", 0)),
    ])
    for idx, (aname, qty) in enumerate([a for a in asset_list if a[1] > 0], start=1):
        writer.writerow([idx, aname, qty])

    csv_bytes = csv_stream.getvalue().encode('utf-8-sig')
    return dxf_bytes, csv_bytes


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))


@app.get("/api/symbols")
async def api_get_symbols():
    return JSONResponse(content={
        "symbols": load_symbols(),
        "infra_aliases": load_infra_aliases()
    })


@app.post("/api/symbols")
async def api_save_symbols(request: Request):
    try:
        new_symbols = await request.json()
        if not isinstance(new_symbols, dict) or not new_symbols:
            raise ValueError("Data simbol tidak valid.")
        save_symbols(new_symbols)
        return JSONResponse(content={"status": "ok", "symbols": new_symbols})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/symbols/add-keyword")
async def api_add_keyword(request: Request):
    try:
        data = await request.json()
        sym_key = data.get("symbol_key", "").strip().upper()
        keyword = data.get("keyword", "").strip().lower()

        if not sym_key or not keyword:
            raise ValueError("Simbol dan kata kunci tidak boleh kosong.")

        symbol_lib = load_symbols()
        infra_aliases = load_infra_aliases()

        if sym_key in ('MH', 'HH', 'HA', 'HB'):
            alist = infra_aliases.setdefault(sym_key, [])
            if keyword not in alist:
                alist.append(keyword)
                save_infra_aliases(infra_aliases)
        elif sym_key in symbol_lib:
            kws = symbol_lib[sym_key].setdefault("keywords", [])
            if keyword not in kws:
                kws.append(keyword)
                save_symbols(symbol_lib)
        else:
            raise ValueError(f"Simbol '{sym_key}' tidak ditemukan.")

        return JSONResponse(content={
            "status": "ok",
            "symbols": symbol_lib,
            "infra_aliases": infra_aliases
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/symbols/reset")
async def api_reset_symbols():
    save_symbols(DEFAULT_SYMBOL_LIBRARY)
    save_infra_aliases(DEFAULT_INFRA_ALIASES)
    return JSONResponse(content={
        "symbols": load_symbols(),
        "infra_aliases": load_infra_aliases()
    })


@app.post("/api/parse-kml")
async def api_parse_kml(file: UploadFile = File(...)):
    try:
        content = await file.read()
        result = parse_kml_bytes(content, file.filename)
        return JSONResponse(content=result)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/export-dxf")
async def api_export_dxf(request: Request):
    try:
        payload = await request.json()
        include_boq = bool(payload.get("include_boq", True))
        raw_filename = payload.get("filename", "BOLERO_MAP_EXPORT")

        base_name = os.path.splitext(os.path.basename(raw_filename))[0].strip()
        base_name = re.sub(r'[<>:"/\\|?*]', '_', base_name) or "BOLERO_MAP_EXPORT"

        dxf_bytes, csv_bytes = generate_dxf_and_boq(payload)

        if include_boq:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f"{base_name}.dxf", dxf_bytes)
                zf.writestr(f"BOQ_{base_name}.csv", csv_bytes)
            zip_buffer.seek(0)

            out_name = f"{base_name}.zip"
            encoded_name = quote(out_name)
            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f"attachment; filename=\"{encoded_name}\"; filename*=UTF-8''{encoded_name}"
                }
            )
        else:
            out_name = f"{base_name}.dxf"
            encoded_name = quote(out_name)
            return StreamingResponse(
                io.BytesIO(dxf_bytes),
                media_type="application/dxf",
                headers={
                    "Content-Disposition": f"attachment; filename=\"{encoded_name}\"; filename*=UTF-8''{encoded_name}"
                }
            )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Gagal membuat file DXF: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)