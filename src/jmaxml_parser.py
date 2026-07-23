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
    """
    種別名からベース名とレベルを抽出する

    例:
      "レベル5大雨特別警報（浸水害）" → ("大雨", 5)
      "レベル4大雨危険警報（浸水害）" → ("大雨", 4)
      "レベル3大雨警報（浸水害）"     → ("大雨", 3)
      "レベル2大雨注意報"             → ("大雨", 2)
      "レベル3暴風警報"               → ("暴風", 3)
      "レベル2雷注意報"               → ("雷", 2)
    """
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

    area_name = head_title.replace("熱中症警戒アラート", "").strip()

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
