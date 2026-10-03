"""menufile (23) and menu (24). docs/research/structs-content.md section 6.

PS3 menus are wider than PC (four local clients): windowDef_t 176, menuDef_t
424 (TEMP, align 8), itemDef_s 280 (align 8), textDef_s 140, listBoxDef_s 700,
editFieldDef_s 48, focusItemDef_s 8. Offsets that the document names only by
position are keyed by offset here.

Loaders: MenuList 0x2562e0 (Ptr 0x256578); menuDef_t Ptr 0x2561e0, struct
0x255cb0; itemDef_s 0x2558e0; itemDefData 0x255598; textDef_s 0x255460; focus
union 0x249df0; ExpressionStatement 0x254768; GenericEventHandler 0x255088;
GenericEventScript 0x254b80; ItemKeyHandler 0x254d70; UIAnimInfo 0x255230.
"""

from __future__ import annotations

from typing import Any

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

RPN_CONSTANT = 0
VAL_STRING = 2


def read_expression(st: XStream, ex: Chunk) -> dict:
    """ExpressionStatement (16): +0 filename, +4 line, +8 numRpn, +0xc rpn [-1]."""
    out: dict = {"filename": st.string(ex, 0), "line": ex.s32(4), "rpn": None}
    if st.follows(ex, 12):
        n = ex.s32(8)
        st.alloc(3)
        table = st.load(12 * n)
        rpn = []
        for r in table.items(12, n):
            item: dict = {"type": r.s32(0), "data_type": r.s32(4), "value": r.u32(8)}
            if item["type"] == RPN_CONSTANT and item["data_type"] == VAL_STRING:
                item["string"] = st.string(r, 8)
            rpn.append(item)
        out["rpn"] = rpn
    return out


def read_script_conditions(st: XStream, c: Chunk) -> list[bytes]:
    """ScriptCondition (16) chain: +0xc next [nz]."""
    chain = [c.bytes()]
    while st.follows(c, 12, owned=True):
        st.alloc(3)
        c = st.load(16)
        chain.append(c.bytes())
    return chain


def read_event_script(st: XStream) -> list[dict]:
    """GenericEventScript (44) chain, the first one read here."""
    scripts = []
    s = st.load(44)
    while True:
        script: dict = {"prerequisites": None}
        if st.follows(s, 0, owned=True):
            st.alloc(3)
            script["prerequisites"] = read_script_conditions(st, st.load(16))
        script["condition"] = read_expression(st, s.sub(4, 16))
        script["action"] = st.string(s, 28)
        script["raw"] = s.bytes()
        scripts.append(script)
        if not st.follows(s, 40, owned=True):
            break
        st.alloc(3)
        s = st.load(44)
    return scripts


def read_event_handler(st: XStream) -> list[dict]:
    """GenericEventHandler (12) chain: +0 name, +4 eventScript [nz], +8 next [nz]."""
    handlers = []
    h = st.load(12)
    while True:
        handler: dict = {"name": st.string(h, 0), "script": None}
        if st.follows(h, 4, owned=True):
            st.alloc(3)
            handler["script"] = read_event_script(st)
        handlers.append(handler)
        if not st.follows(h, 8, owned=True):
            break
        st.alloc(3)
        h = st.load(12)
    return handlers


def read_key_handler(st: XStream) -> list[dict]:
    """ItemKeyHandler (12) chain: +0 key, +4 keyScript [nz], +8 next [nz]."""
    handlers = []
    k = st.load(12)
    while True:
        handler: dict = {"key": k.s32(0), "script": None}
        if st.follows(k, 4, owned=True):
            st.alloc(3)
            handler["script"] = read_event_script(st)
        handlers.append(handler)
        if not st.follows(k, 8, owned=True):
            break
        st.alloc(3)
        k = st.load(12)
    return handlers


ITEM_TYPE_LISTBOX = 4
ITEM_TYPE_MULTI = 10
ITEM_TYPE_DVARENUM = 11
ITEM_TYPE_GAME_MESSAGE_WINDOW = 15
EDITFIELD_TYPES = frozenset((5, 7, 8, 9, 12, 13, 14, 16, 30))
#: Item types whose typeData is a textDef_s.
TEXT_TYPES = frozenset((1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20))
#: Item types whose textDef_s typeData is a focusItemDef_s.
TEXT_FOCUS_TYPES = frozenset((3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 16, 20, 21, 30))
#: Item types whose typeData is an ExpressionStatement (imageDef_s / ownerDrawDef_s).
EXPRESSION_TYPES = frozenset((2, 6))
#: Item types whose typeData is a focusItemDef_s directly.
FOCUS_TYPES = frozenset((19, 21))


def read_list_box(st: XStream) -> dict:
    lb = st.load(700)
    out: dict = {
        "num_columns": lb.s32(60),
        "select_icon": asset_ref(st, lb, 672, AssetType.MATERIAL),
        "background_item_listbox": asset_ref(st, lb, 676, AssetType.MATERIAL),
        "highlight_texture": asset_ref(st, lb, 680, AssetType.MATERIAL),
        "rows": None,
        "raw": lb.bytes(),
    }
    if st.follows(lb, 688, owned=True):
        rows_count, columns = lb.s32(692), lb.s32(60)
        st.alloc(3)
        rows = []
        for row in st.load(24 * rows_count).items(24, rows_count):
            entry: dict = {"cells": None}
            if st.follows(row, 0, owned=True):
                st.alloc(3)
                cells = []
                for c in st.load(12 * columns).items(12, columns):
                    value = None
                    if st.follows(c, 8, owned=True):
                        st.alloc(0)
                        value = st.load(c.s32(4)).bytes()
                    cells.append({"max_chars": c.s32(4), "string_value": value})
                entry["cells"] = cells
            for key, off in (("event_name", 4), ("on_focus_event_name", 8)):
                entry[key] = None
                if st.follows(row, off, owned=True):
                    st.alloc(0)
                    entry[key] = st.load(32).bytes()
            rows.append(entry)
        out["rows"] = rows
    return out


def read_focus_type_data(st: XStream, item_type: int, f: Chunk) -> Any:
    if not st.follows(f, 4, owned=True):
        return None
    if item_type == ITEM_TYPE_LISTBOX:
        st.alloc(3)
        return {"list_box": read_list_box(st)}
    if item_type == ITEM_TYPE_MULTI:
        st.alloc(3)
        md = st.load(396)
        strings = [st.string(md, 4 * i) for i in range(64)]
        return {"multi": {"dvar_list": strings[:32], "dvar_str": strings[32:], "raw": md.bytes()}}
    if item_type in EDITFIELD_TYPES:
        st.alloc(3)
        return {"edit_field": st.load(48).bytes()}
    if item_type == ITEM_TYPE_DVARENUM:
        st.alloc(3)
        e = st.load(4)
        return {"enum_dvar_name": st.string(e, 0)}
    return None


def read_focus_item(st: XStream, item_type: int) -> dict:
    """focusItemDef_s (8 on PS3): +0 onKey [nz], +4 focusTypeData [nz]."""
    f = st.load(8)
    out: dict = {"on_key": None}
    if st.follows(f, 0, owned=True):
        st.alloc(3)
        out["on_key"] = read_key_handler(st)
    out["type_data"] = read_focus_type_data(st, item_type, f)
    return out


def read_item_data(st: XStream, item_type: int, it: Chunk) -> Any:
    if not st.follows(it, 204, owned=True):
        return None
    if item_type in TEXT_TYPES:
        st.alloc(3)
        t = st.load(140)
        text: dict = {"text": st.string(t, 128), "text_exp": None, "type_data": None}
        if st.follows(t, 132, owned=True):
            st.alloc(3)
            text["text_exp"] = read_expression(st, st.load(16))
        if st.follows(t, 136, owned=True):
            if item_type in TEXT_FOCUS_TYPES:
                st.alloc(3)
                text["type_data"] = {"focus": read_focus_item(st, item_type)}
            elif item_type == ITEM_TYPE_GAME_MESSAGE_WINDOW:
                st.alloc(3)
                text["type_data"] = {"game_msg": st.load(8).bytes()}
        text["raw"] = t.bytes()
        return {"text_def": text}
    if item_type in EXPRESSION_TYPES:
        st.alloc(3)
        return {"expression": read_expression(st, st.load(16))}
    if item_type in FOCUS_TYPES:
        st.alloc(3)
        return {"focus": read_focus_item(st, item_type)}
    return None


def read_window(st: XStream, w: Chunk) -> dict:
    """windowDef_t (176): name, group, background (in that load order)."""
    return {
        "name": st.string(w, 0),
        "group": st.string(w, 52),
        "background": asset_ref(st, w, 172, AssetType.MATERIAL),
    }


def read_anim_info(st: XStream) -> dict:
    a = st.load(236)
    out: dict = {"anim_states": None, "raw": a.bytes()}
    if st.follows(a, 4, owned=True):
        n = a.s32(0)
        st.alloc(3)
        table = st.load(4 * n)
        states = []
        for i in range(n):
            state = None
            if st.follows(table, 4 * i, owned=True):
                st.alloc(3)
                p = st.load(108)
                state = {"name": st.string(p, 0), "on_event": None, "raw": p.bytes()}
                if st.follows(p, 104, owned=True):
                    st.alloc(3)
                    state["on_event"] = read_event_handler(st)
            states.append(state)
        out["anim_states"] = states
    return out


def read_item(st: XStream) -> dict:
    it = st.load(280)
    out: dict = {"window": read_window(st, it)}
    out["dvar"] = st.string(it, 188)
    out["dvar_test"] = st.string(it, 192)
    out["enable_dvar"] = st.string(it, 196)
    item_type = it.s32(176)
    out["type"] = item_type
    out["type_data"] = read_item_data(st, item_type, it)
    out["rect_exp_data"] = None
    if st.follows(it, 212, owned=True):
        st.alloc(3)
        r = st.load(64)
        out["rect_exp_data"] = [read_expression(st, r.sub(16 * k, 16)) for k in range(4)]
    out["visible_exp"] = read_expression(st, it.sub(216, 16))
    out["text_align_y_exp"] = read_expression(st, it.sub(248, 16))
    out["on_event"] = None
    if st.follows(it, 268, owned=True):
        st.alloc(3)
        out["on_event"] = read_event_handler(st)
    out["anim_info"] = None
    if st.follows(it, 272, owned=True):
        st.alloc(3)
        out["anim_info"] = read_anim_info(st)
    out["raw"] = it.bytes()
    return out


def read_menu(st: XStream, m: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"window": read_window(st, m)}
    out["name"] = out["window"]["name"]
    out["font"] = st.string(m, 176)
    out["on_open"] = None
    if st.follows(m, 292, owned=True):
        st.alloc(3)
        out["on_open"] = read_event_handler(st)
    out["on_key"] = None
    if st.follows(m, 296, owned=True):
        st.alloc(3)
        out["on_key"] = read_key_handler(st)
    out["visible_exp"] = read_expression(st, m.sub(300, 16))
    out["allowed_binding"] = st.string(m, 336)
    out["sound_name"] = st.string(m, 340)
    out["rect_x_exp"] = read_expression(st, m.sub(384, 16))
    out["rect_y_exp"] = read_expression(st, m.sub(400, 16))
    out["item_count"] = m.s32(188)
    out["items"] = None
    if st.follows(m, 416, owned=True):
        n = m.s32(188)
        st.alloc(3)
        table = st.load(4 * n)
        items = []
        for i in range(n):
            item = None
            if st.follows(table, 4 * i, owned=True):
                st.alloc(7)
                item = read_item(st)
            items.append(item)
        out["items"] = items
    out["header"] = m.bytes()
    st.pop()
    return out


@register
class MenuHandler(Handler):
    asset_type = AssetType.MENU
    header_size = 424
    align = 7

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_menu(st, header)


@register
class MenuFileHandler(Handler):
    """MenuList (12): +0 name, +4 menuCount, +8 menus [nz] (align 4, 4 x count,
    each a menu asset ref)."""

    asset_type = AssetType.MENUFILE
    header_size = 12

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        menus = None
        if st.follows(h, 8, owned=True):
            n = h.s32(4)
            st.alloc(3)
            table = st.load(4 * n)
            menus = [asset_ref(st, table, 4 * i, AssetType.MENU) for i in range(n)]
        st.pop()
        return {"name": name, "menu_count": h.s32(4), "menus": menus}
