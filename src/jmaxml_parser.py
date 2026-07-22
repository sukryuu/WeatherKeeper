import xml.etree.ElementTree as ET
import logging
import re
from collections import defaultdict
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

NS_JMXML = "http://xml.kishou.go.jp/jmaxml1/"
NS_INFO = "http://xml.kishou.go.jp/jmaxml1/informationBasis1/"
NS_BODY = "http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"

# R06形式のControl/Titleに含まれる識別子
R06_IDENTIFIER = "Ｒ０６"


def _find_text(elem: Optional[ET.Element], path: str) -> str:
    if elem is None:
        return ""
    for ns in [NS_JMXML, NS_INFO, NS_BODY]:
        found = elem.find(
            path.replace("default:", f"{{{ns}}}").replace("info:", f"{{{ns}}}")
        )
        if found is not None and found.text:
            return found.text.strip()
    return ""


def extract_kind_and_level(kind_name: str) -> tuple[str, int]:
    """
    R06形式の種別名からベース名とレベルを抽出する

    例:
      "レベル5大雨特別警報（浸水害）" → ("大雨", 5)
      "レベル4大雨危険警報（浸水害）" → ("大雨", 4)
      "レベル3大雨警報（浸水害）"     → ("大雨", 3)
      "レベル2大雨注意報"             → ("大雨", 2)
      "レベル3暴風警報"               → ("暴風", 3)
      "レベル2雷注意報"               → ("雷", 2)
    """
    level = 1

    # "レベル２"〜"レベル５" の全角数字を抽出
    level_match = re.search(r"レベル([２-５])", kind_name)
    if level_match:
        fw_to_hw = str.maketrans("２３４５", "2345")
        level = int(level_match.group(1).translate(fw_to_hw))

    # キーワードによるレベル判定（レベル表記がない場合のフォールバック）
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

    # 種別ベース名の抽出
    base = kind_name
    base = re.sub(r"レベル[２-５]", "", base)
    # ★ R06対応: "危険警報" を "警報" より先に処理
    base = re.sub(r"(特別|危険)?警報|注意報|警戒情報|（.*?）", "", base)
    base = base.strip()

    return base, level


def parse_warning_xml(xml_content: str) -> Optional[Dict[str, Any]]:
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

    # ★ 構造変更: grouped_alerts[base][level][status] = set(areas)
    grouped_alerts = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))

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
                        # ★ Status要素を抽出
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
                                areas.append(area_name)

                if not kinds or not areas:
                    continue
                if all("なし" in k for k, s in kinds):
                    continue

                for k, status in kinds:
                    base, level = extract_kind_and_level(k)
                    if not base:
                        continue
                    for area in areas:
                        grouped_alerts[base][level][status].add(area)
    else:
        logger.warning("Body要素が見つかりませんでした")

    # defaultdict → 通常 dict
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
    }
