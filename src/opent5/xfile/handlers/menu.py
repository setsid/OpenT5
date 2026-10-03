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

from collections.abc import Callable

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

RPN_CONSTANT = 0
VAL_STRING = 2

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


def sub(io: XStream, parent: Chunk, off: int, node: dict, key: str, mask: int = 3):
    """An [nz] pointer to one sub-struct: align and return its node, or None."""
    if io.follows(parent, off, owned=True):
        io.alloc(mask)
        if io.reading:
            node[key] = {}
        return node[key]
    if io.reading:
        node[key] = None
    return None


def chain(
    io: XStream,
    node: dict,
    key: str,
    size: int,
    next_off: int,
    each: Callable[[XStream, Chunk, dict], None],
) -> None:
    """A linked list the loader reads element by element: LS size, the element's
    pointers, then +next_off [nz] -> align 4 and the next one. node[key] lists them."""
    elements = io.children(node, key)
    index = 0
    while True:
        element = io.child(elements, index)
        c = io.load(size, element, "raw")
        each(io, c, element)
        if not io.follows(c, next_off, owned=True):
            return
        io.alloc(3)
        index += 1


def expression(io: XStream, ex: Chunk, node: dict) -> None:
    """ExpressionStatement (16): +0 filename, +4 line, +8 numRpn, +0xc rpn [-1]."""
    io.string(ex, 0, node, "filename")
    for r, rpn in items(io, ex, 12, 3, 12, ex.s32(8), node, "rpn") or ():
        if r.s32(0) == RPN_CONSTANT and r.s32(4) == VAL_STRING:
            io.string(r, 8, rpn, "string")


def _no_pointers(io: XStream, c: Chunk, node: dict) -> None:
    pass


def event_script(io: XStream, s: Chunk, node: dict) -> None:
    """GenericEventScript (44): prerequisites, condition, action; +0x28 next."""
    prerequisites = sub(io, s, 0, node, "prerequisites")
    if prerequisites is not None:
        chain(io, prerequisites, "conditions", 16, 12, _no_pointers)
    expression(io, s.sub(4, 16), node.setdefault("condition", {}))
    io.string(s, 28, node, "action")


def event_handler(io: XStream, h: Chunk, node: dict) -> None:
    """GenericEventHandler (12): +0 name, +4 eventScript [nz]; +8 next."""
    io.string(h, 0, node, "name")
    script = sub(io, h, 4, node, "script")
    if script is not None:
        chain(io, script, "scripts", 44, 40, event_script)


def key_handler(io: XStream, k: Chunk, node: dict) -> None:
    """ItemKeyHandler (12): +0 key, +4 keyScript [nz]; +8 next."""
    script = sub(io, k, 4, node, "script")
    if script is not None:
        chain(io, script, "scripts", 44, 40, event_script)


def event_handlers(io: XStream, parent: Chunk, off: int, node: dict, key: str) -> None:
    target = sub(io, parent, off, node, key)
    if target is not None:
        chain(io, target, "handlers", 12, 8, event_handler)


def key_handlers(io: XStream, parent: Chunk, off: int, node: dict, key: str) -> None:
    target = sub(io, parent, off, node, key)
    if target is not None:
        chain(io, target, "handlers", 12, 8, key_handler)


def list_box(io: XStream, node: dict) -> None:
    lb = io.load(700, node, "raw")
    asset_ref(io, lb, 672, AssetType.MATERIAL, node, "select_icon")
    asset_ref(io, lb, 676, AssetType.MATERIAL, node, "background_item_listbox")
    asset_ref(io, lb, 680, AssetType.MATERIAL, node, "highlight_texture")
    columns = lb.s32(60)
    for row, element in items(io, lb, 688, 3, 24, lb.s32(692), node, "rows", owned=True) or ():
        for c, cell in items(io, row, 0, 3, 12, columns, element, "cells", owned=True) or ():
            array(io, c, 8, 0, c.s32(4), cell, "string_value", owned=True)
        array(io, row, 4, 0, 32, element, "event_name", owned=True)
        array(io, row, 8, 0, 32, element, "on_focus_event_name", owned=True)


def focus_type_data(io: XStream, item_type: int, f: Chunk, node: dict) -> None:
    if not io.follows(f, 4, owned=True):
        return
    data = node.setdefault("type_data", {})
    if item_type == ITEM_TYPE_LISTBOX:
        io.alloc(3)
        list_box(io, data.setdefault("list_box", {}))
    elif item_type == ITEM_TYPE_MULTI:
        io.alloc(3)
        multi = data.setdefault("multi", {})
        md = io.load(396, multi, "raw")
        strings = multi.setdefault("strings", {})
        for i in range(64):
            io.string(md, 4 * i, strings, i)
    elif item_type in EDITFIELD_TYPES:
        io.alloc(3)
        io.load(48, data, "edit_field")
    elif item_type == ITEM_TYPE_DVARENUM:
        io.alloc(3)
        enum = data.setdefault("enum_dvar", {})
        e = io.load(4, enum, "raw")
        io.string(e, 0, enum, "name")


def focus_item(io: XStream, item_type: int, node: dict) -> None:
    """focusItemDef_s (8 on PS3): +0 onKey [nz], +4 focusTypeData [nz]."""
    f = io.load(8, node, "raw")
    key_handlers(io, f, 0, node, "on_key")
    focus_type_data(io, item_type, f, node)


def item_data(io: XStream, item_type: int, it: Chunk, node: dict) -> None:
    if not io.follows(it, 204, owned=True):
        return
    data = node.setdefault("type_data", {})
    if item_type in TEXT_TYPES:
        io.alloc(3)
        text = data.setdefault("text_def", {})
        t = io.load(140, text, "raw")
        io.string(t, 128, text, "text")
        exp = sub(io, t, 132, text, "text_exp")
        if exp is not None:
            expression(io, io.load(16, exp, "raw"), exp)
        if io.follows(t, 136, owned=True):
            if item_type in TEXT_FOCUS_TYPES:
                io.alloc(3)
                focus_item(io, item_type, text.setdefault("focus", {}))
            elif item_type == ITEM_TYPE_GAME_MESSAGE_WINDOW:
                io.alloc(3)
                io.load(8, text, "game_msg")
    elif item_type in EXPRESSION_TYPES:
        io.alloc(3)
        exp = data.setdefault("expression", {})
        expression(io, io.load(16, exp, "raw"), exp)
    elif item_type in FOCUS_TYPES:
        io.alloc(3)
        focus_item(io, item_type, data.setdefault("focus", {}))


def window(io: XStream, w: Chunk, node: dict) -> None:
    """windowDef_t (176): name, group, background (in that load order)."""
    io.string(w, 0, node, "name")
    io.string(w, 52, node, "group")
    asset_ref(io, w, 172, AssetType.MATERIAL, node, "background")


def anim_info(io: XStream, node: dict) -> None:
    a = io.load(236, node, "raw")
    table = array(io, a, 4, 3, 4 * a.s32(0), node, "anim_state_ptrs", owned=True)
    if table is None:
        return
    states = io.children(node, "anim_states")
    for i in range(a.s32(0)):
        state = io.child(states, i)
        if io.follows(table, 4 * i, owned=True):
            io.alloc(3)
            p = io.load(108, state, "raw")
            io.string(p, 0, state, "name")
            event_handlers(io, p, 104, state, "on_event")


def item(io: XStream, node: dict) -> None:
    it = io.load(280, node, "raw")
    window(io, it, node.setdefault("window", {}))
    io.string(it, 188, node, "dvar")
    io.string(it, 192, node, "dvar_test")
    io.string(it, 196, node, "enable_dvar")
    item_data(io, it.s32(176), it, node)
    rect = sub(io, it, 212, node, "rect_exp_data")
    if rect is not None:
        r = io.load(64, rect, "raw")
        for k in range(4):
            expression(io, r.sub(16 * k, 16), rect.setdefault(k, {}))
    expression(io, it.sub(216, 16), node.setdefault("visible_exp", {}))
    expression(io, it.sub(248, 16), node.setdefault("text_align_y_exp", {}))
    event_handlers(io, it, 268, node, "on_event")
    info = sub(io, it, 272, node, "anim_info")
    if info is not None:
        anim_info(io, info)


def menu_body(io: XStream, m: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    win = node.setdefault("window", {})
    window(io, m, win)
    io.note(node, "name", win.get("name"))
    io.string(m, 176, node, "font")
    event_handlers(io, m, 292, node, "on_open")
    key_handlers(io, m, 296, node, "on_key")
    expression(io, m.sub(300, 16), node.setdefault("visible_exp", {}))
    io.string(m, 336, node, "allowed_binding")
    io.string(m, 340, node, "sound_name")
    expression(io, m.sub(384, 16), node.setdefault("rect_x_exp", {}))
    expression(io, m.sub(400, 16), node.setdefault("rect_y_exp", {}))
    count = m.s32(188)
    table = array(io, m, 416, 3, 4 * count, node, "item_ptrs", owned=True)
    if table is not None:
        elements = io.children(node, "items")
        for i in range(count):
            element = io.child(elements, i)
            if io.follows(table, 4 * i, owned=True):
                io.alloc(7)
                item(io, element)
    io.pop()


@register
class MenuHandler(Handler):
    asset_type = AssetType.MENU
    header_size = 424
    align = 7

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        menu_body(io, header, node)

    def name_of(self, node) -> str | None:
        return node.get("window", {}).get("name") if isinstance(node, dict) else None


@register
class MenuFileHandler(Handler):
    """MenuList (12): +0 name, +4 menuCount, +8 menus [nz] (align 4, 4 x count,
    each a menu asset ref)."""

    asset_type = AssetType.MENUFILE
    header_size = 12

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        count = h.s32(4)
        table = array(io, h, 8, 3, 4 * count, node, "menu_ptrs", owned=True)
        if table is not None:
            menus = io.children(node, "menus")
            for i in range(count):
                if io.reading:
                    menus.append(None)
                asset_ref(io, table, 4 * i, AssetType.MENU, menus, i)
        io.pop()
