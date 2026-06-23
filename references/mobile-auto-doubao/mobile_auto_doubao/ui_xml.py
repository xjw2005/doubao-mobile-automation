import re
from xml.etree import ElementTree


URL_RE = re.compile(r"https?://[^\s\"'<>，。；、)）\]]+", re.IGNORECASE)


def parse_bounds(bounds: str | None) -> dict[str, int] | None:
    """Parse an Android bounds string into numeric coordinates."""
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "centerX": (left + right) // 2,
        "centerY": (top + bottom) // 2,
    }


def collect_nodes(xml_text: str) -> list[dict]:
    """Convert a uiautomator XML dump into a flat list of node dictionaries."""
    nodes = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        bounds = node.attrib.get("bounds", "")
        nodes.append({
            "text": node.attrib.get("text", ""),
            "resource_id": node.attrib.get("resource-id", ""),
            "content_desc": node.attrib.get("content-desc", ""),
            "class": node.attrib.get("class", ""),
            "bounds": bounds,
            "clickable": node.attrib.get("clickable", ""),
            "enabled": node.attrib.get("enabled", ""),
            "selected": node.attrib.get("selected", ""),
            "checked": node.attrib.get("checked", ""),
            "parsedBounds": parse_bounds(bounds),
        })
    return nodes


def find_nodes(nodes: list[dict], resource_id: str | None = None, text_contains: str | None = None, text_exact: str | None = None) -> list[dict]:
    """Filter nodes by resource id and/or text content."""
    found = []
    for node in nodes:
        if resource_id and node.get("resource_id") != resource_id:
            continue
        text = node.get("text", "")
        if text_contains and text_contains not in text:
            continue
        if text_exact and text_exact != text:
            continue
        if node.get("parsedBounds"):
            found.append(node)
    return found


def visible_texts(nodes: list[dict]) -> list[str]:
    """Return the text values for nodes that currently expose text."""
    return [node.get("text", "") for node in nodes if node.get("text")]


def extract_urls_from_text(text: str) -> list[str]:
    """Extract HTTP(S) URLs from free-form text."""
    return URL_RE.findall(text or "")


def extract_urls_from_nodes(nodes: list[dict]) -> list[str]:
    """Extract unique URLs from the text-like fields of all nodes."""
    urls = []
    seen = set()
    for node in nodes:
        for field in ("text", "content_desc", "resource_id"):
            for url in extract_urls_from_text(node.get(field, "")):
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def visible_source_items(nodes: list[dict], resource_id: str) -> list[dict]:
    """Return visible source items keyed by their title and center point."""
    items = []
    for node in nodes:
        bounds = node.get("parsedBounds")
        if node.get("resource_id") == resource_id and node.get("text") and bounds:
            items.append({
                "title": node["text"],
                "bounds": node["bounds"],
                "centerX": bounds["centerX"],
                "centerY": bounds["centerY"],
            })
    return items
