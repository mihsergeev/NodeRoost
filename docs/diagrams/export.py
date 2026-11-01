#!/usr/bin/env python3
"""Export the landing page's existing localized diagrams as standalone SVG.

Run from any directory: python docs/diagrams/export.py
Only the en/ and ru/ SVG files beside this script are written. The source
geometry and text are retained; the landing page's dark colors are embedded.
"""

from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
SITE = HERE.parent.parent / "landing" / "site"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
SLUGS = (
    "lan-to-lan", "behind-nat", "private-services", "contractor-access",
    "device-exit", "server-exit", "ip-allowlist", "home-network",
    "server-to-server", "split-tunnel", "provider-change", "enrollment",
    "private-ssh", "recovery-access", "subnet-router",
)
PALETTE = {
    "--example-node-bg": "#18221d", "--example-active-bg": "#1e2a23",
    "--example-border": "#2d3a31", "--example-text": "#eef3ef",
    "--example-muted": "#a5b3aa", "--example-muted-line": "#819287",
    "--example-accent": "#9aefb4",
}
# These are the architecture-specific CSS overrides in examples.css.
ARCHITECTURE_COLORS = {
    ("fill", "#161C26"): PALETTE["--example-node-bg"],
    ("fill", "#212836"): PALETTE["--example-node-bg"],
    ("stroke", "#212836"): PALETTE["--example-border"],
    ("fill", "#3E4A5E"): PALETTE["--example-muted-line"],
    ("stroke", "#3E4A5E"): PALETTE["--example-muted-line"],
    ("fill", "#8B98AC"): PALETTE["--example-muted"],
    ("fill", "#A9B5C6"): PALETTE["--example-muted"],
    ("fill", "#E4EAF2"): PALETTE["--example-text"],
}
SAFE_TAGS = {"svg", "g", "rect", "text", "line", "path", "circle", "ellipse", "polyline", "polygon", "tspan"}
SAFE_ATTRIBUTES = {
    "class", "viewBox", "role", "aria-label", "aria-hidden", "x", "y",
    "width", "height", "rx", "ry", "x1", "x2", "y1", "y2", "cx", "cy",
    "r", "d", "points", "fill", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "font-family", "font-size",
    "font-weight", "text-anchor", "dominant-baseline", "transform", "opacity",
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(markup):
    parser = PlainText()
    parser.feed(markup)
    return " ".join("".join(parser.parts).split())


def one(pattern, text):
    matches = re.findall(pattern, text, re.S)
    if len(matches) != 1:
        raise ValueError(f"Expected one source element, got {len(matches)}: {pattern}")
    return matches[0]


def standalone(markup, title, description, slug, language, padding):
    if "<!" in markup or "<?" in markup:
        raise ValueError("Declarations and entities are not allowed in source SVG")
    source = ET.fromstring(markup)
    x, y, width, height = map(float, source.attrib["viewBox"].split())
    if x != 0 or y != 0:
        raise ValueError("Source diagrams must have a zero-origin viewBox")
    for element in source.iter():
        if element.tag not in SAFE_TAGS or set(element.attrib) - SAFE_ATTRIBUTES:
            raise ValueError(f"Unexpected SVG element or attributes: {element.tag}")
        for name, value in list(element.attrib.items()):
            value = re.sub(r"var\((--[a-z-]+)\)", lambda m: PALETTE[m[1]], value)
            if slug == "architecture":
                value = ARCHITECTURE_COLORS.get((name, value), value)
            if any(marker in value.lower() for marker in ("var(", "url(", "javascript:", "http:", "https:")):
                raise ValueError(f"External or unresolved SVG attribute: {name}")
            element.set(name, value)
        element.tag = f"{{{NS}}}{element.tag}"
    outer_width, outer_height = width + 2 * padding, height + 2 * padding
    svg = ET.Element(f"{{{NS}}}svg", {
        "width": f"{outer_width:g}", "height": f"{outer_height:g}",
        "viewBox": f"0 0 {outer_width:g} {outer_height:g}", "role": "img",
        "aria-labelledby": f"{slug}-title {slug}-desc",
        "{http://www.w3.org/XML/1998/namespace}lang": language,
    })
    ET.SubElement(svg, f"{{{NS}}}title", {"id": f"{slug}-title"}).text = title
    ET.SubElement(svg, f"{{{NS}}}desc", {"id": f"{slug}-desc"}).text = description
    ET.SubElement(svg, f"{{{NS}}}rect", {
        "width": f"{outer_width:g}", "height": f"{outer_height:g}", "fill": "#101714",
    })
    group = ET.SubElement(svg, f"{{{NS}}}g", {"transform": f"translate({padding} {padding})"})
    group.extend(list(source))
    ET.indent(svg, space="  ")
    return ET.tostring(svg, encoding="unicode") + "\n"


def main():
    count = 0
    for language, path in (("ru", SITE / "index.html"), ("en", SITE / "en" / "index.html")):
        html = path.read_text(encoding="utf-8")
        cards = re.findall(r'<div class="example-card">(.*?)</div>', html, re.S)
        if len(cards) != len(SLUGS):
            raise ValueError(f"{path}: expected {len(SLUGS)} example cards, got {len(cards)}")
        diagrams = []
        for slug, card in zip(SLUGS, cards):
            title = plain(one(r"<h3[^>]*>(.*?)</h3>", card))
            description = " ".join(plain(p) for p in re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", card, re.S))
            diagrams.append((slug, one(r"(<svg\b.*?</svg>)", card), title, description, 12))
        figure = one(r'(<figure class="architecture-diagram"[^>]*>.*?</figure>)', html)
        markup = one(r"(<svg\b.*?</svg>)", figure)
        title = plain(one(r'<figure[^>]*aria-label="([^"]+)"', figure))
        description = ET.fromstring(markup).attrib["aria-label"]
        caption = plain(one(r"<figcaption[^>]*>(.*?)</figcaption>", figure))
        diagrams.append(("architecture", markup, title, description + " " + caption, 16))
        output = HERE / language
        output.mkdir(exist_ok=True)
        for slug, markup, title, description, padding in diagrams:
            data = standalone(markup, title, description, slug, language, padding)
            (output / f"{slug}.svg").write_text(data, encoding="utf-8", newline="\n")
            count += 1
    print(f"Exported {count} standalone localized SVG diagrams")


if __name__ == "__main__":
    main()
