import xml.etree.ElementTree as ET
import logging
import re
from collections import defaultdict
from typing import Dict, Optional, Any
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
JST = timezone(timedelta(hours=9))

NS_JMXML = "http://xml.kishou.go.jp/jmaxml1/"
NS_INFO = "http://xml.kishou.go.jp/jmaxml1/informationBasis1/"
NS_BODY = "http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"
JMX_NAMESPACES = {
    "jmx": "http://xml.kishou.go.jp/jmaxml1/",
    "jmx_eb": "http://xml.kishou.go.jp/jmaxml1/elementBasis1/",
    "jmx_add": "http://xml.kishou.go.jp/jmaxml1/addition1/",
    "jmx_i": "http://xml.kishou.go.jp/jmaxml1/informationBasis1/",
    "jmx_b": "http://xml.kishou.go.jp/jmaxml1/body/meteorology1/",
}

R06_IDENTIFIER = "Ｒ０６"


def _ensure_jmx_namespaces(xml_content: str) -> str:
    for prefix, uri in JMX_NAMESPACES.items():
        if f"xmlns:{prefix}=" not in xml_content:
            xml_content = re.sub(
                r"(<Report\b)",
                rf'\1 xmlns:{prefix}="{uri}"',
                xml_content,
                count=1,
            )
    return xml_content


def _find_text(elem: Optional[ET.Element], path: str) -> str:
    if elem is None:
        return ""
    candidates = [path.replace("default:", "").replace("info:", "")]
    for ns in [NS_JMXML, NS_INFO, NS_BODY]:
        candidates.append(
            path.replace("default:", f"{{{ns}}}").replace("info:", f"{{{ns}}}")
        )
    for p in candidates:
        found = elem.find(p)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def extract_kind_and_level(kind_name: str) -> tuple[str, int]:
    level = 1

    level_match = re.search(r"レベル([２-５])", kind_name)
    if level_match:
        fw_to_hw = str.maketrans("２３４５", "2345")
        level = int(level_match.group(1).translate(fw_to_hw))

    if "特別警報" in kind_name:
        level = max(level, 5)
    elif "危険警報" in kind_name:
        level = max(level, 4)
    elif "警戒情報" in kind_name or "氾濫危険情報" in kind_name:
        level = max(level, 4)
    elif "警報" in kind_name:
        level = max(level, 3)
    elif "注意報" in kind_name:
        level = max(level, 2)

    base = kind_name
    base = re.sub(r"レベル[２-５]", "", base)
    base = re.sub(r"(特別|危険)?警報|注意報|警戒情報|（.*?）", "", base)
    base = base.strip()

    return base, level


def parse_warning_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")

    if R06_IDENTIFIER not in control_title:
        logger.debug(f"R06形式ではないためスキップ: {control_title}")
        return None

    head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    info_type = _find_text(head, "info:InfoType")
    info_kind = _find_text(head, "info:InfoKind")
    event_id = _find_text(head, "info:EventID")
    head_title = _find_text(head, "info:Title")

    headline_text = ""
    headline = root.find(f".//{{{NS_INFO}}}Headline")
    if headline is None:
        headline = root.find(f".//{{{NS_JMXML}}}Headline")

    if headline is not None:
        text_elem = headline.find(f"{{{NS_INFO}}}Text")
        if text_elem is None:
            text_elem = headline.find(f"{{{NS_JMXML}}}Text")
        if text_elem is not None and text_elem.text:
            headline_text = text_elem.text.strip()

    grouped_alerts = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    area_levels = {}

    body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    if body is not None:
        for warning in body:
            if not warning.tag.endswith("Warning"):
                continue

            warning_type = warning.attrib.get("type", "")
            if "気象警報・注意報" not in warning_type:
                continue
            if "市町村等" not in warning_type:
                continue

            for item in warning:
                if not item.tag.endswith("Item"):
                    continue

                kinds = []  # [(name, status), ...]
                areas = []

                for child in item:
                    if child.tag.endswith("Kind"):
                        name_elem = child.find(f"{{{NS_BODY}}}Name")
                        if name_elem is None:
                            name_elem = child.find(f"{{{NS_JMXML}}}Name")
                        status_elem = child.find(f"{{{NS_BODY}}}Status")
                        if status_elem is None:
                            status_elem = child.find(f"{{{NS_JMXML}}}Status")

                        if name_elem is not None and name_elem.text:
                            kind_name = name_elem.text.strip()
                            kind_status = (
                                status_elem.text.strip()
                                if status_elem is not None and status_elem.text
                                else "発表"
                            )
                            kinds.append((kind_name, kind_status))

                    elif child.tag.endswith("Area"):
                        name_elem = child.find(f"{{{NS_BODY}}}Name")
                        if name_elem is None:
                            name_elem = child.find(f"{{{NS_JMXML}}}Name")
                        code_elem = child.find(f"{{{NS_BODY}}}Code")
                        if code_elem is None:
                            code_elem = child.find(f"{{{NS_JMXML}}}Code")

                        if name_elem is not None and name_elem.text:
                            area_name = name_elem.text.strip()
                            area_code = (
                                code_elem.text.strip()
                                if code_elem is not None and code_elem.text
                                else ""
                            )
                            if len(area_code) == 7:
                                areas.append((area_name, area_code))

                if not kinds or not areas:
                    continue
                if all("なし" in k for k, s in kinds):
                    continue

                for k, status in kinds:
                    base, level = extract_kind_and_level(k)
                    if not base:
                        continue
                    for area_name, area_code in areas:
                        grouped_alerts[base][level][status].add(area_name)
                        if status != "解除":
                            area_levels[area_code] = max(
                                area_levels.get(area_code, 0), level
                            )
    else:
        logger.warning("Body要素が見つかりませんでした")

    grouped_dict = {}
    for base, levels in grouped_alerts.items():
        grouped_dict[base] = {}
        for lv, statuses in levels.items():
            grouped_dict[base][lv] = {
                st: sorted(areas) for st, areas in statuses.items()
            }

    logger.debug(f"抽出結果: {grouped_dict}")

    return {
        "event_id": event_id,
        "control_title": control_title,
        "head_title": head_title,
        "info_type": info_type,
        "info_kind": info_kind,
        "is_cancel": (info_type == "取消"),
        "headline_text": headline_text,
        "grouped_alerts": grouped_dict,
        "area_levels": area_levels,
    }


def _format_target_label(target_dt: str) -> str:
    try:
        dt = datetime.fromisoformat(target_dt)
        target_date = dt.date()
        today = datetime.now(JST).date()
        date_str = target_date.strftime("%Y/%m/%d")
        if target_date == today + timedelta(days=1):
            return f"明日 ({date_str})"
        elif target_date == today:
            return f"今日 ({date_str})"
        return date_str
    except (ValueError, TypeError):
        return target_dt[:10] if target_dt else ""


def _extract_wbgt(text: str) -> list:
    m = re.search(
        r"日最高暑さ指数（ＷＢＧＴ）（予測）］(.*?)暑さ指数（ＷＢＧＴ：",
        text,
        re.DOTALL,
    )
    if not m:
        return []
    section = m.group(1)
    pairs = re.findall(r"([^\d０-９\s、]+)\s*([０-９]+)", section)
    return [(place, int(num.translate(FW_DIGITS))) for place, num in pairs]


def _extract_temperature(text: str) -> list:
    m = re.search(r"の予想最高気温］(.*?)(?:この情報|$)", text, re.DOTALL)
    if not m:
        return []
    section = m.group(1)
    pairs = re.findall(r"([^\d０-９\s、]+)\s*([０-９]+)\s*度", section)
    return [(place, int(num.translate(FW_DIGITS))) for place, num in pairs]


def parse_heatstroke_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")
    if "熱中症" not in control_title or "警戒アラート" not in control_title:
        logger.debug(f"熱中症警戒アラートではないためスキップ: {control_title}")
        return None
    is_special = "特別警戒" in control_title

    head = root.find("Head")
    if head is None:
        head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")

    area_name = head_title.replace("熱中症特別警戒アラート", "").replace("熱中症警戒アラート", "").strip()

    body = root.find("Body")
    if body is None:
        body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    comment_text = ""
    if body is not None:
        for elem in body.iter():
            if elem.tag.endswith("Text") and elem.attrib.get("type") == "本文":
                if elem.text:
                    comment_text = elem.text.strip()
                break

    call_to_action = comment_text.split("＜")[0].strip() if comment_text else ""

    wbgt_readings = _extract_wbgt(comment_text)
    temp_readings = _extract_temperature(comment_text)

    logger.debug(
        f"熱中症警戒アラート抽出: area={area_name}, "
        f"wbgt={wbgt_readings}, temp={temp_readings}"
    )

    return {
        "area_name": area_name,
        "head_title": head_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "target_label": _format_target_label(target_dt),
        "call_to_action": call_to_action,
        "wbgt_readings": wbgt_readings,
        "temp_readings": temp_readings,
        "is_special": is_special,
    }


def _descendants(elem):
    for child in elem:
        yield child
        yield from _descendants(child)


def _child_text(elem, tag_suffix):
    for child in elem:
        if child.tag.endswith(tag_suffix) and child.text:
            return child.text.strip()
    return ""


def _commentary_section_text(body, section_type):
    for infos in body:
        if not infos.tag.endswith("MeteorologicalInfos"):
            continue
        if infos.attrib.get("type") != section_type:
            continue
        texts = []
        for elem in _descendants(infos):
            if elem.tag.endswith("Text") and elem.attrib.get("type") == "本文":
                if elem.text and elem.text.strip():
                    texts.append(elem.text.strip())
        return "\n".join(texts)
    return ""


def _commentary_observations(body):
    results = []
    for infos in body:
        if not infos.tag.endswith("MeteorologicalInfos"):
            continue
        if infos.attrib.get("type") != "観測実況":
            continue
        for item in infos.iter():
            if not item.tag.endswith("Item"):
                continue
            station_name = None
            sentence = None
            for child in item:
                if child.tag.endswith("Station"):
                    station_name = _child_text(child, "Name")
                elif child.tag.endswith("Kind"):
                    for d in _descendants(child):
                        if d.tag.endswith("Sentence") and d.text and d.text.strip():
                            sentence = d.text.strip()
                            break
            if station_name and sentence:
                results.append({"station": station_name, "value": sentence})
    return results


def _commentary_forecasts(body):
    forecasts = []
    for infos in body:
        if not infos.tag.endswith("MeteorologicalInfos"):
            continue
        if infos.attrib.get("type") != "予想":
            continue
        for tsi in infos:
            if not tsi.tag.endswith("TimeSeriesInfo"):
                continue
            time_names = {}
            element_text = ""
            header_texts = {}
            area_items = []
            for child in tsi:
                if child.tag.endswith("TimeDefines"):
                    for td in child:
                        if td.tag.endswith("TimeDefine"):
                            tid = td.attrib.get("timeId")
                            nm = _child_text(td, "Name")
                            if tid and nm:
                                time_names[tid] = nm
                elif child.tag.endswith("Item"):
                    area_name = None
                    values = {}
                    for sub in _descendants(child):
                        if sub.tag.endswith("Area"):
                            area_name = _child_text(sub, "Name")
                        elif sub.tag.endswith("Text"):
                            t = sub.attrib.get("type")
                            if t == "気象要素" and sub.text:
                                element_text = sub.text.strip()
                            elif t == "時系列解説":
                                rid = sub.attrib.get("refID")
                                if rid and sub.text:
                                    header_texts[rid] = sub.text.strip()
                        elif sub.tag.endswith("Precipitation"):
                            rid = sub.attrib.get("refID")
                            desc = sub.attrib.get("description")
                            if rid and desc:
                                values[rid] = desc
                    if area_name is not None:
                        area_items.append({"name": area_name, "values": values})

            periods = []
            for rid in sorted(
                header_texts.keys(),
                key=lambda x: int(x) if x.isdigit() else 0,
            ):
                label = time_names.get(rid, header_texts[rid])
                area_vals = [
                    f"{ai['name']} {ai['values'][rid]}"
                    for ai in area_items
                    if rid in ai["values"]
                ]
                if area_vals:
                    periods.append({"label": label, "areas": area_vals})
            if periods:
                forecasts.append({"element": element_text, "periods": periods})
    return forecasts


def parse_commentary_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")

    head = root.find("Head")
    if head is None:
        head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    if "解説" not in head_title:
        logger.debug(f"気象解説情報ではないためスキップ: {head_title}")
        return None

    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")

    if "全般" in head_title:
        scope = "全般"
    elif "地方" in head_title:
        scope = "地方"
    else:
        scope = "府県"

    headline = root.find(".//Headline")
    if headline is None:
        headline = root.find(f".//{{{NS_INFO}}}Headline")

    headline_text = ""
    tags = []
    if headline is not None:
        t = headline.find("Text")
        if t is None:
            t = headline.find(f"{{{NS_INFO}}}Text")
        if t is not None and t.text:
            headline_text = t.text.strip()
        for elem in headline.iter():
            if elem.tag.endswith("Condition") and elem.text and elem.text.strip():
                tags = elem.text.strip().split()
                break

    body = root.find("Body")
    if body is None:
        body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    overview = ""
    disaster_matters = ""
    additional = ""
    observations = []
    forecasts = []
    comment_text = ""

    if body is not None:
        has_struct = any(c.tag.endswith("MeteorologicalInfos") for c in body)
        if has_struct:
            overview = _commentary_section_text(body, "概況")
            disaster_matters = _commentary_section_text(body, "防災事項")
            additional = _commentary_section_text(body, "付加情報")
            observations = _commentary_observations(body)
            forecasts = _commentary_forecasts(body)
        else:
            for elem in body.iter():
                if elem.tag.endswith("Text") and elem.attrib.get("type") == "本文":
                    if elem.text and elem.text.strip():
                        comment_text = elem.text.strip()
                    break

    return {
        "scope": scope,
        "control_title": control_title,
        "head_title": head_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "headline_text": headline_text,
        "tags": tags,
        "overview": overview,
        "disaster_matters": disaster_matters,
        "additional": additional,
        "comment_text": comment_text,
        "observations": observations,
        "forecasts": forecasts,
    }


def parse_early_warning_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")
    if "早期注意情報" not in control_title:
        logger.debug(f"早期注意情報ではないためスキップ: {control_title}")
        return None

    head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")

    body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    time_names: Dict[str, str] = {}
    areas_data: list = []

    if body is not None:
        for infos in body:
            if not infos.tag.endswith("MeteorologicalInfos"):
                continue
            if infos.attrib.get("type") != "区域予報":
                continue
            for tsi in infos:
                if not tsi.tag.endswith("TimeSeriesInfo"):
                    continue
                for child in tsi:
                    if child.tag.endswith("TimeDefines"):
                        for td in child:
                            if td.tag.endswith("TimeDefine"):
                                tid = td.attrib.get("timeId", "")
                                nm = _child_text(td, "Name")
                                if tid and nm:
                                    time_names[tid] = nm
                    elif child.tag.endswith("Item"):
                        area_name = ""
                        area_code = ""
                        kinds: list = []
                        for sub in child:
                            if sub.tag.endswith("Area"):
                                area_name = _child_text(sub, "Name")
                                area_code = _child_text(sub, "Code")
                            elif sub.tag.endswith("Kind"):
                                for prop in sub:
                                    if not prop.tag.endswith("Property"):
                                        continue
                                    kind_type = _child_text(prop, "Type")
                                    if not kind_type:
                                        continue
                                    ranks = []
                                    for part in prop:
                                        if not part.tag.endswith(
                                            "PossibilityRankOfWarningPart"
                                        ):
                                            continue
                                        for rw in part:
                                            if not rw.tag.endswith(
                                                "PossibilityRankOfWarning"
                                            ):
                                                continue
                                            rid = rw.attrib.get("refID", "")
                                            rank = (
                                                rw.text.strip()
                                                if rw.text
                                                else ""
                                            )
                                            if rank in ("高", "中"):
                                                ranks.append(
                                                    {
                                                        "time_id": rid,
                                                        "time_name": time_names.get(
                                                            rid, rid
                                                        ),
                                                        "rank": rank,
                                                    }
                                                )
                                    if ranks:
                                        kinds.append(
                                            {"type": kind_type, "ranks": ranks}
                                        )
                        if area_name and kinds:
                            areas_data.append(
                                {
                                    "name": area_name,
                                    "code": area_code,
                                    "kinds": kinds,
                                }
                            )

    if info_type == "取消":
        areas_data = []

    return {
        "head_title": head_title,
        "control_title": control_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "time_names": time_names,
        "areas": areas_data,
    }


def parse_record_rain_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")
    if "記録的短時間大雨情報" not in control_title:
        logger.debug(f"記録的短時間大雨情報ではないためスキップ: {control_title}")
        return None

    head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")
    serial = _find_text(head, "info:Serial")

    headline_text = ""
    headline = root.find(f".//{{{NS_INFO}}}Headline")
    if headline is None:
        headline = root.find(f".//{{{NS_JMXML}}}Headline")
    if headline is not None:
        text_elem = headline.find(f"{{{NS_INFO}}}Text")
        if text_elem is None:
            text_elem = headline.find(f"{{{NS_JMXML}}}Text")
        if text_elem is not None and text_elem.text:
            headline_text = text_elem.text.strip()

    area_name = ""
    area_code = ""
    body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")
    if body is not None:
        for warning in body:
            if not warning.tag.endswith("Warning"):
                continue
            for item in warning:
                if not item.tag.endswith("Item"):
                    continue
                for child in item:
                    if child.tag.endswith("Area"):
                        area_name = _child_text(child, "Name")
                        area_code = _child_text(child, "Code")

    return {
        "head_title": head_title,
        "control_title": control_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "serial": serial,
        "headline_text": headline_text,
        "area_name": area_name,
        "area_code": area_code,
    }


FLOOD_LEVEL_MAP = {
    "５": 5,
    "４": 4,
    "３": 3,
    "２": 2,
    "１": 1,
}


def parse_flood_forecast_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")
    if "指定河川洪水予報" not in control_title:
        logger.debug(f"指定河川洪水予報ではないためスキップ: {control_title}")
        return None

    head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")
    serial = _find_text(head, "info:Serial")

    headline_text = ""
    kind_name = ""
    river_area_name = ""
    pref_names: list = []

    headline = root.find(f".//{{{NS_INFO}}}Headline")
    if headline is None:
        headline = root.find(f".//{{{NS_JMXML}}}Headline")
    if headline is not None:
        text_elem = headline.find(f"{{{NS_INFO}}}Text")
        if text_elem is None:
            text_elem = headline.find(f"{{{NS_JMXML}}}Text")
        if text_elem is not None and text_elem.text:
            headline_text = text_elem.text.strip()

        for info in headline:
            if not info.tag.endswith("Information"):
                continue
            info_type_attr = info.attrib.get("type", "")
            for item in info:
                if not item.tag.endswith("Item"):
                    continue
                for child in item:
                    if child.tag.endswith("Kind"):
                        nm = _child_text(child, "Name")
                        if nm and not kind_name:
                            kind_name = nm
                    elif child.tag.endswith("Areas"):
                        if "予報区域" in info_type_attr:
                            for area in child:
                                if area.tag.endswith("Area"):
                                    nm = _child_text(area, "Name")
                                    if nm and not river_area_name:
                                        river_area_name = nm
                        elif "府県予報区" in info_type_attr:
                            for area in child:
                                if area.tag.endswith("Area"):
                                    nm = _child_text(area, "Name")
                                    if nm and nm not in pref_names:
                                        pref_names.append(nm)

    level = 0
    for fw_digit, lv in FLOOD_LEVEL_MAP.items():
        if f"レベル{fw_digit}" in kind_name or f"レベル{fw_digit}" in head_title:
            level = lv
            break

    body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    main_texts: list = []
    affected_cities: list = []
    rainfall_text = ""
    rainfall_series: list = []
    water_stations: list = []

    if body is not None:
        for warning in body:
            if not warning.tag.endswith("Warning"):
                continue
            for item in warning:
                if not item.tag.endswith("Item"):
                    continue
                item_text = ""
                station_name = ""
                station_location = ""
                for child in item:
                    if child.tag.endswith("Kind"):
                        for prop in child:
                            if prop.tag.endswith("Property"):
                                ptype = _child_text(prop, "Type")
                                if ptype == "主文":
                                    for t in prop:
                                        if t.tag.endswith("Text") and t.text:
                                            item_text = t.text.strip()
                    elif child.tag.endswith("Stations"):
                        for st in child:
                            if st.tag.endswith("Station"):
                                station_name = _child_text(st, "Name")
                                station_location = _child_text(st, "Location")
                    elif child.tag.endswith("Areas"):
                        for area in child:
                            if not area.tag.endswith("Area"):
                                continue
                            city = _child_text(area, "City")
                            pref = _child_text(area, "Prefecture")
                            if city:
                                label = f"{pref}{city}" if pref else city
                                if label not in affected_cities:
                                    affected_cities.append(label)
                if item_text:
                    main_texts.append(
                        {
                            "station": station_name,
                            "location": station_location,
                            "text": item_text,
                        }
                    )

        for infos in body:
            if not infos.tag.endswith("MeteorologicalInfos"):
                continue
            infos_type = infos.attrib.get("type", "")

            if infos_type == "雨量情報":
                for child in infos:
                    if child.tag.endswith("MeteorologicalInfo"):
                        for item in child:
                            if not item.tag.endswith("Item"):
                                continue
                            for kind in item:
                                if not kind.tag.endswith("Kind"):
                                    continue
                                for prop in kind:
                                    if prop.tag.endswith("Property"):
                                        for t in prop:
                                            if t.tag.endswith("Text") and t.text:
                                                rainfall_text = t.text.strip()

                    elif child.tag.endswith("TimeSeriesInfo"):
                        time_names: Dict[str, str] = {}
                        for tsi_child in child:
                            if tsi_child.tag.endswith("TimeDefines"):
                                for td in tsi_child:
                                    if td.tag.endswith("TimeDefine"):
                                        tid = td.attrib.get("timeId", "")
                                        nm = _child_text(td, "Name")
                                        if tid and nm:
                                            time_names[tid] = nm
                            elif tsi_child.tag.endswith("Item"):
                                area_name = ""
                                for sub in _descendants(tsi_child):
                                    if sub.tag.endswith("Area"):
                                        area_name = _child_text(sub, "Name")
                                    elif sub.tag.endswith("Precipitation"):
                                        rid = sub.attrib.get("refID", "")
                                        unit = sub.attrib.get("unit", "")
                                        val = (
                                            sub.text.strip()
                                            if sub.text
                                            else ""
                                        )
                                        if rid and val:
                                            rainfall_series.append(
                                                {
                                                    "label": time_names.get(rid, rid),
                                                    "value": val,
                                                    "unit": unit,
                                                    "area": area_name,
                                                }
                                            )

            elif infos_type == "水位・流量情報":
                for child in infos:
                    if not child.tag.endswith("TimeSeriesInfo"):
                        continue
                    time_names = {}
                    for tsi_child in child:
                        if tsi_child.tag.endswith("TimeDefines"):
                            for td in tsi_child:
                                if td.tag.endswith("TimeDefine"):
                                    tid = td.attrib.get("timeId", "")
                                    nm = _child_text(td, "Name")
                                    if tid and nm:
                                        time_names[tid] = nm
                        elif tsi_child.tag.endswith("Item"):
                            st_name = ""
                            st_location = ""
                            for sub in tsi_child:
                                if sub.tag.endswith("Station"):
                                    st_name = _child_text(sub, "Name")
                                    st_location = _child_text(sub, "Location")
                            series: list = []
                            for sub in _descendants(tsi_child):
                                if not sub.tag.endswith("WaterLevel"):
                                    continue
                                rid = sub.attrib.get("refID", "")
                                wl_type = sub.attrib.get("type", "")
                                unit = sub.attrib.get("unit", "")
                                val = sub.text.strip() if sub.text else ""
                                if not rid or not val:
                                    continue
                                existing = next(
                                    (s for s in series if s["time_id"] == rid),
                                    None,
                                )
                                if existing is None:
                                    existing = {
                                        "time_id": rid,
                                        "label": time_names.get(rid, rid),
                                        "level_m": "",
                                        "level_rank": "",
                                        "discharge": "",
                                    }
                                    series.append(existing)
                                if wl_type == "水位":
                                    existing["level_m"] = val
                                    existing["unit"] = unit
                                elif wl_type == "レベル":
                                    existing["level_rank"] = val
                                elif wl_type == "流量":
                                    existing["discharge"] = val
                                    existing["discharge_unit"] = unit
                            if st_name and series:
                                water_stations.append(
                                    {
                                        "station": st_name,
                                        "location": st_location,
                                        "series": series,
                                    }
                                )

    return {
        "head_title": head_title,
        "control_title": control_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "serial": serial,
        "headline_text": headline_text,
        "kind_name": kind_name,
        "level": level,
        "river_area_name": river_area_name,
        "pref_names": pref_names,
        "main_texts": main_texts,
        "affected_cities": affected_cities,
        "rainfall_text": rainfall_text,
        "rainfall_series": rainfall_series,
        "water_stations": water_stations,
    }


def parse_volcano_eruption_xml(xml_content: str) -> Optional[Dict[str, Any]]:
    xml_content = _ensure_jmx_namespaces(xml_content)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        logger.error(f"XMLのパースに失敗しました: {e}")
        return None

    control_title = _find_text(root, "default:Control/default:Title")
    if "噴火速報" not in control_title:
        logger.debug(f"噴火速報ではないためスキップ: {control_title}")
        return None

    head = root.find(f"{{{NS_INFO}}}Head")
    if head is None:
        head = root.find(f"{{{NS_JMXML}}}Head")

    head_title = _find_text(head, "info:Title")
    info_type = _find_text(head, "info:InfoType")
    target_dt = _find_text(head, "info:TargetDateTime")

    volcano_name = ""
    body = root.find(f"{{{NS_BODY}}}Body")
    if body is None:
        body = root.find(f"{{{NS_JMXML}}}Body")

    if body is not None:
        for volcano_info in body:
            if not volcano_info.tag.endswith("VolcanoInfo"):
                continue
            if volcano_info.attrib.get("type") != "噴火速報":
                continue
            for item in volcano_info:
                if not item.tag.endswith("Item"):
                    continue
                for child in item:
                    if child.tag.endswith("Areas"):
                        for area in child:
                            if area.tag.endswith("Area"):
                                volcano_name = _child_text(area, "Name")

    volcano_activity = ""
    affected_areas = []
    if body is not None:
        for content in body:
            if content.tag.endswith("VolcanoInfoContent"):
                for child in content:
                    if child.tag.endswith("VolcanoActivity") and child.text:
                        volcano_activity = child.text.strip()
            elif content.tag.endswith("VolcanoInfo"):
                if content.attrib.get("type") == "噴火速報（対象市町村等）":
                    for item in content:
                        if item.tag.endswith("Item"):
                            for child in item:
                                if child.tag.endswith("Areas"):
                                    for area in child:
                                        if area.tag.endswith("Area"):
                                            name = _child_text(area, "Name")
                                            if name and name not in affected_areas:
                                                affected_areas.append(name)

    return {
        "head_title": head_title,
        "control_title": control_title,
        "info_type": info_type,
        "target_datetime": target_dt,
        "volcano_name": volcano_name,
        "volcano_activity": volcano_activity,
        "affected_areas": affected_areas,
    }
