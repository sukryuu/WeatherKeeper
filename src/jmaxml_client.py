import requests
import xml.etree.ElementTree as ET
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "WeatherDiscordBot/1.0 (your_email@example.com)"}


def fetch_atom_feed(url: str) -> List[Dict[str, str]]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        root = ET.fromstring(response.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        entries = []
        for entry in root.findall("atom:entry", ns):
            id_elem = entry.find("atom:id", ns)
            title_elem = entry.find("atom:title", ns)
            link_elem = entry.find("atom:link", ns)
            updated_elem = entry.find("atom:updated", ns)

            entries.append(
                {
                    "id": id_elem.text if id_elem is not None and id_elem.text else "",
                    "title": (
                        title_elem.text
                        if title_elem is not None and title_elem.text
                        else ""
                    ),
                    "link": (
                        link_elem.attrib.get("href", "")
                        if link_elem is not None
                        else ""
                    ),
                    "updated": (
                        updated_elem.text
                        if updated_elem is not None and updated_elem.text
                        else ""
                    ),
                }
            )

        return entries

    except requests.exceptions.RequestException as e:
        logger.error(f"Atomフィードの取得に失敗しました: {url}, エラー: {e}")
        return []
    except ET.ParseError as e:
        logger.error(f"AtomフィードのXMLパースに失敗しました: {url}, エラー: {e}")
        return []


def fetch_xml_content(url: str) -> Optional[str]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"XML本文の取得に失敗しました: {url}, エラー: {e}")
        return None
