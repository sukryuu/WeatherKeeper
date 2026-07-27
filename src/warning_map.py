import json
import os
import io
import re
import logging
import platform
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor

import matplotlib
import matplotlib.font_manager as fm

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
import numpy as np  # noqa: E402

import config  # noqa: E402

logger = logging.getLogger(__name__)

CITY_GEOJSON_FILE = os.path.join(config.DATA_DIR, "city_warning.geojson")
PREF_GEOJSON_FILE = os.path.join(config.DATA_DIR, "pref_warning.geojson")

LEVEL_COLORS = {
    5: "#000000",
    4: "#7A008A",
    3: "#B40000",
    2: "#DBAF00",
}
LEVEL_LABELS = {
    5: "特別警報",
    4: "危険警報",
    3: "警報",
    2: "注意報",
}
HEATSTROKE_COLORS = {
    "alert": "#7A008A",
    "special": "#000000",
}

LAND_COLOR = "#3D3C3C"
SEA_COLOR = "#19191A"
EDGE_COLOR = "#BDBDBD"
EDGE_WIDTH = 0.2

PREF_EDGE_COLOR = "#929292"
PREF_EDGE_WIDTH = 0.6

BASE_FIG_SIZE = 10
MAP_ASPECT_RATIO = 1.5

if platform.system() == "Windows":
    font_path = "C:/Windows/Fonts/NotoSansJP-Regular.otf"
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams["font.family"] = "Noto Sans CJK JP"
    else:
        logger.warning(f"フォントファイルが見つかりません: {font_path}")
        plt.rcParams["font.family"] = "Meiryo"
else:
    _linux_font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for _p in _linux_font_paths:
        if os.path.exists(_p):
            fm.fontManager.addfont(_p)
            break
    _available = {f.name for f in fm.fontManager.ttflist}
    _chosen = None
    for _family in [
        "Noto Sans CJK JP",
        "Noto Sans JP",
        "IPAexGothic",
        "VL Gothic",
        "DejaVu Sans",
    ]:
        if _family in _available:
            _chosen = _family
            break
    if _chosen:
        plt.rcParams["font.family"] = _chosen
    else:
        logger.warning("日本語フォント未検出")

plt.rcParams["text.color"] = "#FFFFFF"
plt.rcParams["axes.titlecolor"] = "#FFFFFF"
plt.rcParams["legend.labelcolor"] = "#000000"

_city_features: Optional[List[dict]] = None
_pref_features: Optional[List[dict]] = None
_map_executor: Optional[ProcessPoolExecutor] = None


def get_map_executor() -> ProcessPoolExecutor:
    global _map_executor
    if _map_executor is None:
        _map_executor = ProcessPoolExecutor(max_workers=1)
    return _map_executor


def _load_city_features() -> List[dict]:
    global _city_features
    if _city_features is not None:
        return _city_features
    if not os.path.exists(CITY_GEOJSON_FILE):
        logger.error(f"市町村GeoJSONが見つかりません: {CITY_GEOJSON_FILE}")
        _city_features = []
        return _city_features
    try:
        with open(CITY_GEOJSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        features: List[dict] = data.get("features", [])
        logger.info(f"市町村GeoJSONを読み込みました: {len(features)} features")
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"市町村GeoJSONの読み込みに失敗: {e}")
        features = []
    _city_features = features
    return features


def _load_pref_features() -> List[dict]:
    global _pref_features
    if _pref_features is not None:
        return _pref_features
    if not os.path.exists(PREF_GEOJSON_FILE):
        logger.warning(f"都道府県GeoJSONが見つかりません: {PREF_GEOJSON_FILE}")
        _pref_features = []
        return _pref_features
    try:
        with open(PREF_GEOJSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        features: List[dict] = data.get("features", [])
        logger.info(f"都道府県GeoJSONを読み込みました: {len(features)} features")
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"都道府県GeoJSONの読み込みに失敗: {e}")
        features = []
    _pref_features = features
    return features


def _get_feature_code(props: dict) -> str:
    code = props.get("regioncode")
    return str(code).strip() if code is not None else ""


def _extract_polygons(geometry: dict) -> List[List[Tuple[float, float]]]:
    polygons = []
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if geom_type == "Polygon":
        if coords and coords[0]:
            polygons.append([(x, y) for x, y in coords[0]])
    elif geom_type == "MultiPolygon":
        for polygon in coords:
            if polygon and polygon[0]:
                polygons.append([(x, y) for x, y in polygon[0]])
    return polygons


PREF_NAME_TO_CODE: Dict[str, str] = {}


def _build_pref_mapping():
    global PREF_NAME_TO_CODE
    if PREF_NAME_TO_CODE:
        return
    features = _load_city_features()
    for f in features:
        props = f.get("properties", {})
        regionname = props.get("regionname", "")
        regioncode = props.get("regioncode", "")
        if not regionname or not regioncode:
            continue
        m = re.match(r"^(.+?[都道府県])", regionname)
        if m:
            pref_name = m.group(1)
            pref_code = str(regioncode)[:2]
            PREF_NAME_TO_CODE[pref_name] = pref_code


def _match_heatstroke_code(props: dict, area_names: List[str]) -> bool:
    pref_code = props.get("pref_code", "")
    if not pref_code:
        return False
    for area_name in area_names:
        if not area_name:
            continue
        m = re.search(r"^(.+?)[（(](.+?)[）)]$", area_name)
        target_pref_name = m.group(1) if m else area_name
        target_code = PREF_NAME_TO_CODE.get(target_pref_name)
        if target_code and target_code == pref_code:
            return True
    return False


def create_warning_map_image(
    area_levels: Dict[str, int],
    heatstroke_area_names: Optional[List[str]] = None,
    heatstroke_special: bool = False,
    title: str = "気象警報・注意報 発表範囲",
) -> Optional[bytes]:

    logger.info(f"[DEBUG] heatstroke_area_names: {heatstroke_area_names}")

    features = _load_city_features()
    if not features:
        return None

    fig, ax = plt.subplots(figsize=(BASE_FIG_SIZE, BASE_FIG_SIZE / MAP_ASPECT_RATIO))
    fig.patch.set_facecolor(SEA_COLOR)
    ax.set_facecolor(SEA_COLOR)
    fig.subplots_adjust(left=0.01, right=0.99, bottom=0.01, top=0.93)

    drawn_x: List[float] = []
    drawn_y: List[float] = []

    hs_color = HEATSTROKE_COLORS["special" if heatstroke_special else "alert"]

    # 1. 市町村の描画（警報レベルのみ。ここでは熱中症は処理しない）
    for feature in features:
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        if not geometry:
            continue
        polygons = _extract_polygons(geometry)
        if not polygons:
            continue
        code = _get_feature_code(props)
        level = area_levels.get(code)
        if level and level in LEVEL_COLORS:
            color = LEVEL_COLORS[level]
        else:
            color = LAND_COLOR
        for poly_coords in polygons:
            patch = MplPolygon(
                poly_coords,
                closed=True,
                facecolor=color,
                edgecolor=EDGE_COLOR,
                linewidth=EDGE_WIDTH,
                zorder=1,
            )
            ax.add_patch(patch)
            if color != LAND_COLOR:
                xs, ys = zip(*poly_coords)
                drawn_x.extend(xs)
                drawn_y.extend(ys)

    _build_pref_mapping()
    if heatstroke_area_names:
        for feature in _load_pref_features():
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            if not geometry:
                continue
            if not _match_heatstroke_code(props, heatstroke_area_names):
                continue
            for poly_coords in _extract_polygons(geometry):
                patch = MplPolygon(
                    poly_coords,
                    closed=True,
                    facecolor=hs_color,
                    edgecolor="none",
                    linewidth=0,
                    zorder=1,
                )
                ax.add_patch(patch)
                xs, ys = zip(*poly_coords)
                drawn_x.extend(xs)
                drawn_y.extend(ys)

    # 3. 都道府県境界線の描画
    for feature in _load_pref_features():
        geometry = feature.get("geometry", {})
        if not geometry:
            continue
        for poly_coords in _extract_polygons(geometry):
            patch = MplPolygon(
                poly_coords,
                closed=True,
                facecolor="none",
                edgecolor=PREF_EDGE_COLOR,
                linewidth=PREF_EDGE_WIDTH,
                zorder=2,
            )
            ax.add_patch(patch)

    if drawn_x and drawn_y:
        min_x0, max_x0 = min(drawn_x), max(drawn_x)
        min_y0, max_y0 = min(drawn_y), max(drawn_y)
        pad_x = (max_x0 - min_x0) * 0.1 + 0.1
        pad_y = (max_y0 - min_y0) * 0.1 + 0.1
    else:
        min_x0, max_x0 = 122.0, 154.0
        min_y0, max_y0 = 20.0, 46.0
        pad_x = pad_y = 0.0

    min_x0 -= pad_x
    max_x0 += pad_x
    min_y0 -= pad_y
    max_y0 += pad_y

    center_x = (min_x0 + max_x0) / 2
    center_y = (min_y0 + max_y0) / 2
    span_x = max_x0 - min_x0
    span_y = max_y0 - min_y0

    cos_lat = np.cos(np.radians(center_y))
    real_w = span_x * cos_lat
    real_h = span_y

    if real_w / real_h < MAP_ASPECT_RATIO:
        span_x = (real_h * MAP_ASPECT_RATIO) / cos_lat
    else:
        span_y = real_w / MAP_ASPECT_RATIO

    ax.set_xlim(center_x - span_x / 2, center_x + span_x / 2)
    ax.set_ylim(center_y - span_y / 2, center_y + span_y / 2)
    ax.set_aspect(1.0 / cos_lat)
    ax.set_title(title, fontsize=14)
    ax.axis("off")

    legend_elements = []
    has_warning_levels = any(lv in LEVEL_COLORS for lv in area_levels.values())
    if has_warning_levels:
        for lv in sorted(LEVEL_COLORS.keys(), reverse=True):
            legend_elements.append(
                Line2D(
                    [0],
                    [0],
                    marker="s",
                    color="w",
                    markerfacecolor=LEVEL_COLORS[lv],
                    markersize=12,
                    label=LEVEL_LABELS[lv],
                )
            )
    if heatstroke_area_names:
        hs_label = (
            "熱中症特別警戒アラート" if heatstroke_special else "熱中症警戒アラート"
        )
        legend_elements.append(
            Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                markerfacecolor=hs_color,
                markersize=12,
                label=hs_label,
            )
        )
    if legend_elements:
        ax.legend(handles=legend_elements, loc="lower left", fontsize=9)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90, facecolor=SEA_COLOR)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
