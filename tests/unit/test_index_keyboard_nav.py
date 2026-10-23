"""Homepage Tab order visits each pool, then that pool's favorite button.

Safari's default Tab skips links that have no explicit tabindex, and a button
nested inside a link is not a separate, valid stop. Each pool card must
therefore expose a link to that pool (tabindex 0, named with the pool name)
and only then its favorite button.

Each city's pools are also a list under that city's heading, in the same order
as the ItemList structured data a crawler reads.
"""

import json
from collections import defaultdict
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

from api.catalog import get_pools
from api.city_display import CITY_DISPLAY

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


def _tabindex(attrs: dict[str, str]) -> int | None:
    raw = attrs.get("tabindex")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _tabbable(tag: str, attrs: dict[str, str]) -> bool:
    index = _tabindex(attrs)
    if index is not None and index < 0:
        return False
    if tag == "a":
        return bool(attrs.get("href")) or (index is not None and index >= 0)
    if tag == "input":
        return attrs.get("type", "").lower() != "hidden" and "disabled" not in attrs
    if tag in {"button", "select", "textarea"}:
        return "disabled" not in attrs
    return index is not None and index >= 0


def _focus_stop(tag: str, attrs: dict[str, str], classes: list[str]) -> dict:
    if tag == "a":
        return {
            "kind": "link",
            "href": attrs.get("href", ""),
            "tabindex": attrs.get("tabindex"),
            "text_parts": [],
        }
    if tag == "button" and "star-btn" in classes:
        return {"kind": "star", "uid": attrs.get("data-uid", "")}
    return {"kind": tag, "tabindex": attrs.get("tabindex")}


class _PoolCardTabOrder(HTMLParser):
    """Collects the sequential focus stops inside each homepage pool card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._stack: list[dict] = []
        self._rawtext: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._rawtext:
            return
        if tag in {"script", "style"}:
            self._rawtext = tag
            return
        parsed = {key: "" if value is None else value for key, value in attrs}
        classes = parsed.get("class", "").split()
        parent = self._stack[-1]["card"] if self._stack else None
        card = parent
        if "pool-card" in classes:
            card = {
                "uid": parsed.get("data-uid", ""),
                "focusables": [],
                "star_inside_link": False,
            }
            self.cards.append(card)
        focus = None
        if card is not None and _tabbable(tag, parsed):
            inside_link = any(frame["tag"] == "a" for frame in self._stack)
            focus = _focus_stop(tag, parsed, classes)
            card["focusables"].append(focus)
            if focus["kind"] == "star" and inside_link:
                card["star_inside_link"] = True
        if tag not in _VOID_TAGS:
            self._stack.append({"tag": tag, "card": card, "focus": focus})

    def handle_endtag(self, tag: str) -> None:
        if self._rawtext:
            if tag == self._rawtext:
                self._rawtext = None
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._rawtext:
            return
        for frame in reversed(self._stack):
            focus = frame.get("focus")
            if focus and focus.get("kind") == "link":
                focus["text_parts"].append(data)
                return

    def close(self) -> None:
        super().close()
        for card in self.cards:
            for stop in card["focusables"]:
                parts = stop.pop("text_parts", None)
                if parts is not None:
                    stop["text"] = "".join(parts).strip()


@pytest.fixture
async def client():
    from api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as connection:
        yield connection


async def test_tab_visits_pool_link_then_its_favorite_button(client):
    response = await client.get("/")
    assert response.status_code == 200

    parser = _PoolCardTabOrder()
    parser.feed(response.text)
    parser.close()

    pools = {pool["uid"]: pool for pool in get_pools()}
    assert {card["uid"] for card in parser.cards} == set(pools)

    for card in parser.cards:
        pool = pools[card["uid"]]
        assert card["focusables"] == [
            {
                "kind": "link",
                "href": f"/bad/{pool['uid']}",
                "tabindex": "0",
                "text": pool["name"],
            },
            {"kind": "star", "uid": pool["uid"]},
        ], pool["uid"]
        assert card["star_inside_link"] is False, pool["uid"]


def _cities_in_page_order():
    by_city = defaultdict(list)
    for pool in get_pools():
        by_city[pool.get("city", "zurich")].append(pool)
    keys = sorted(by_city, key=lambda city: (city != "zurich", city))
    return [
        {
            "key": key,
            "label": CITY_DISPLAY.get(key, key.title()),
            "pools": by_city[key],
        }
        for key in keys
    ]


class _CityLists(HTMLParser):
    """Reads the homepage outline: one h1, then a section per pool group."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1: list[str] = []
        self.sections: list[dict] = []
        self.item_lists: list[dict] = []
        self._stack: list[dict] = []
        self._raw: str | None = None
        self._raw_type = ""
        self._raw_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._raw:
            return
        parsed = {key: "" if value is None else value for key, value in attrs}
        if tag in {"script", "style"}:
            self._raw = tag
            self._raw_type = parsed.get("type", "")
            self._raw_buf = []
            return
        classes = parsed.get("class", "").split()
        parent = self._stack[-1] if self._stack else None
        frame: dict = {"tag": tag, "classes": classes, "text": []}
        if tag == "h1":
            self.h1.append("")
            frame["h1"] = True
        elif "section-block" in classes:
            section = {
                "tag": tag,
                "id": parsed.get("id", ""),
                "heading": "",
                "children": [],
                "items": [],
            }
            self.sections.append(section)
            frame["section"] = section
        elif parent and parent.get("section") and tag in {"h2", "ul"}:
            parent["section"]["children"].append(tag)
            if tag == "h2":
                frame["heading"] = parent["section"]
            elif "pool-grid" in classes:
                frame["grid"] = parent["section"]
                frame["grid_id"] = parsed.get("id", "")
                parent["section"]["grid_id"] = parsed.get("id", "")
        elif parent and parent.get("grid") and parent["tag"] == "ul":
            item = {"tag": tag, "links": []}
            parent["grid"]["items"].append(item)
            frame["item"] = item
        else:
            item_frame = next(
                (
                    ancestor
                    for ancestor in reversed(self._stack)
                    if ancestor.get("item")
                ),
                None,
            )
            if item_frame and tag == "a":
                link = {"href": parsed.get("href", ""), "text": ""}
                item_frame["item"]["links"].append(link)
                frame["link"] = link
        if tag not in _VOID_TAGS:
            self._stack.append(frame)

    def handle_endtag(self, tag: str) -> None:
        if self._raw:
            if tag == self._raw:
                if self._raw == "script" and self._raw_type == "application/ld+json":
                    try:
                        data = json.loads("".join(self._raw_buf))
                    except json.JSONDecodeError:
                        data = None
                    if isinstance(data, dict) and data.get("@type") == "ItemList":
                        self.item_lists.append(data)
                self._raw = None
            return
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._raw:
            if self._raw == "script" and self._raw_type == "application/ld+json":
                self._raw_buf.append(data)
            return
        for frame in reversed(self._stack):
            if frame.get("h1"):
                self.h1[-1] += data
                return
            if frame.get("heading") is not None and frame["tag"] == "h2":
                frame["heading"]["heading"] += data
                return
            if frame.get("link") is not None:
                frame["link"]["text"] += data
                return


async def test_each_city_lists_its_pools_under_its_heading(client):
    response = await client.get("/")
    assert response.status_code == 200

    parser = _CityLists()
    parser.feed(response.text)
    parser.close()

    cities = _cities_in_page_order()
    assert [text.strip() for text in parser.h1] == [
        "Schweizer Schwimmbäder – Live-Auslastung & Besucherzahlen"
    ]
    assert [section["heading"].strip() for section in parser.sections] == [
        "⭐ Favoriten",
        *[f"Schwimmbäder {city['label']}" for city in cities],
    ]

    favorites = parser.sections[0]
    assert favorites["tag"] == "section"
    assert favorites["id"] == "favorites-section"
    assert favorites["children"] == ["h2", "ul"]
    assert favorites["grid_id"] == "favorites-grid"
    assert favorites["items"] == []

    lists_by_name = {item["name"]: item for item in parser.item_lists}
    for city, section in zip(cities, parser.sections[1:], strict=True):
        heading = f"Schwimmbäder {city['label']}"
        assert section["tag"] == "section", heading
        assert section["children"] == ["h2", "ul"], heading
        assert section["grid_id"] == f"pool-grid-{city['key']}", heading
        assert [item["tag"] for item in section["items"]] == ["li"] * len(city["pools"])
        assert [item["links"] for item in section["items"]] == [
            [{"href": f"/bad/{pool['uid']}", "text": pool["name"]}]
            for pool in city["pools"]
        ], heading
        structured = lists_by_name[heading]
        assert structured["numberOfItems"] == len(city["pools"])
        assert [
            (entry["position"], entry["url"], entry["name"])
            for entry in structured["itemListElement"]
        ] == [
            (index, f"https://badifrei.ch/bad/{pool['uid']}", pool["name"])
            for index, pool in enumerate(city["pools"], start=1)
        ]
