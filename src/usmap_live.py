"""Writes a mapping file (.usmap) from the running Phantom Blade Zero, read from outside (read-only), with the
Blueprint types that are loaded at that moment.

Why: UE4SS's own dump with Blueprint types (DumpUSMAP(), Keybinds Ctrl+Num6) crashes this game on enum properties
without an enum (UE4SS-RE/RE-UE4SS#1483). This script does what UE4SS's USMapGenerator does
(UE4SS/src/USMapGenerator/Generator.cpp, commit cdbc0b66: the same objects, property types, name rules, PPTH and
EATR), with three changes:
- an FEnumProperty without an enum is written as its underlying number type (the game stores such a value as a
  plain number); a ByteProperty without an enum is a ByteProperty, as in UE4SS;
- class default objects (Default__Class, Default__ScriptStruct) are left out;
- a struct whose property chain does not read cleanly (the game changed it during the read) is left out and named.

It opens the process with PROCESS_VM_READ and PROCESS_QUERY_INFORMATION only and writes nothing into it. The
layout is Phantom Blade Zero's (Steam build 26266538; the same layout as UE4SS's custom config for the game,
UE4SS-RE/RE-UE4SS#1484): UStruct has 0x50 more bytes after ChildProperties, FField has Next and Owner swapped, the
FFieldClass name is at 0x30. The addresses of GUObjectArray and the FName block table are RVAs of that build and
are checked before use.

Usage: python -I usmap_live.py <exe path of the process> <out.usmap> [--native-only]
"""
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from process_memory import Mem, find_pid, i32, module_base, q  # noqa: E402
import usmap_io  # noqa: E402
from usmap_io import CONTAINERS, SIMPLE, usmap_name  # noqa: E402

# Steam build 26266538 (image base 0x140000000): GUObjectArray 0x14ddb3c40, FName block table 0x14dd11ad0.
GUOBJECTARRAY_RVA = 0xDDB3C40
NAME_BLOCKS_RVA = 0xDD11AD0
NAME_BLOCK_BYTES = 0x20000

# TUObjectArray in this game's order (at GUObjectArray + 0x10): NumChunks 0x4, Objects 0x8, NumElements 0x10.
OBJECTS = 0x10
PER_CHUNK = 64 * 1024
ITEM = 0x18
ITEM_OBJECT = 0x8
# UObjectBase
OBJ_FLAGS, OBJ_CLASS, OBJ_NAME, OBJ_OUTER = 0x8, 0x10, 0x18, 0x20
RF_CDO_OR_ARCHETYPE = 0x10 | 0x20
# UStruct and the classes after it (+0x50 against the 5.7 template)
STRUCT_SUPER, STRUCT_CHILD_PROPS = 0x40, 0x50
SCRIPTSTRUCT_FLAGS = 0x100
CLASS_FLAGS, CLASS_CAST_FLAGS = 0x124, 0x128
CASTCLASS_UENUM, CASTCLASS_USCRIPTSTRUCT, CASTCLASS_UCLASS = 0x4, 0x10, 0x20
# UEnum (5.7: FNameData = names pointer, values pointer, count)
ENUM_NAMES, ENUM_VALUES, ENUM_COUNT, ENUM_FLAGS = 0x40, 0x48, 0x50, 0x59
# FField (Next and Owner swapped against the template), FFieldClass, FProperty
FIELD_CLASS, FIELD_NEXT, FIELD_OWNER, FIELD_NAME = 0x8, 0x10, 0x18, 0x20
FIELDCLASS_SUPER, FIELDCLASS_NAME = 0x20, 0x30
PROP_ARRAY_DIM, PROP_FLAGS = 0x30, 0x38
PROP_INNER_A, PROP_INNER_B = 0x70, 0x78  # Byte/Struct/Set/Map key/Enum underlying/Optional; Array inner/Map value/Enum enum
PTR_MASK = ~0x7
MAX_FIELDS = 4000

class Broken(Exception):
    pass


class Game:
    def __init__(self, exe):
        pid = find_pid(exe)
        if not pid:
            sys.exit(f"no running process for {exe}")
        self.mem = Mem(pid)
        base = module_base(pid, exe)
        if not base:
            sys.exit("the exe module was not found in the process")
        self.guo = base + GUOBJECTARRAY_RVA
        table = base + NAME_BLOCKS_RVA
        self.blocks = []
        for k in range(8192):
            b = q(self.mem.read(table + 8 * k, 8), 0)
            if not b:
                break
            self.blocks.append(b)
        self.block_data = {}
        head = self.mem.read(self.blocks[0], 6) if self.blocks else None
        if not head or head[2:6] != b"None" or struct.unpack_from("<H", head)[0] >> 6 != 4:
            sys.exit(f"no FName block table at {table:#x} (another game build? see the RVAs at the top)")
        self.names = {}
        print(f"pid {pid}, exe at {base:#x}, GUObjectArray {self.guo:#x}, {len(self.blocks)} name blocks")

    def read(self, address, size):
        data = self.mem.read(address, size)
        if data is None:
            raise Broken(f"unreadable {address:#x}")
        return data

    def fname(self, blob, off):
        ident, number = struct.unpack_from("<II", blob, off)
        text = self.names.get(ident)
        if text is None:
            block, offset = ident >> 16, (ident & 0xFFFF) * 2
            if block >= len(self.blocks):
                raise Broken(f"name id {ident:#x}")
            data = self.block_data.get(block)
            if data is None:
                data = self.block_data[block] = self.read(self.blocks[block], NAME_BLOCK_BYTES)
            header = struct.unpack_from("<H", data, offset)[0]
            length = header >> 6
            raw = data[offset + 2: offset + 2 + length * (2 if header & 1 else 1)]
            text = raw.decode("utf-16-le" if header & 1 else "latin-1")
            self.names[ident] = text
        return text if number == 0 else f"{text}_{number - 1}"


def main():
    sys.stdout.reconfigure(errors="backslashreplace")  # game names can be Chinese; the console may be cp1252
    exe, out = sys.argv[1], Path(sys.argv[2])
    native_only = "--native-only" in sys.argv[3:]
    start = time.time()
    g = Game(exe)

    # All objects: pointer -> UObjectBase bytes.
    head = g.read(g.guo + OBJECTS, 0x18)
    n_chunks, chunk_table, count = i32(head, 0x4), q(head, 0x8), i32(head, 0x10)
    chunks = struct.unpack(f"<{n_chunks}Q", g.read(chunk_table, 8 * n_chunks))
    objects = []
    for c, chunk in enumerate(chunks):
        n = min(PER_CHUNK, count - c * PER_CHUNK)
        data = g.read(chunk, n * ITEM)
        for k in range(n):
            o = q(data, k * ITEM + ITEM_OBJECT)
            if o:
                b = g.mem.read(o, 0x28)
                if b:
                    objects.append((o, b))
    base_of = dict(objects)
    print(f"{len(objects)} objects read ({count} slots)")

    # Metaclasses: the class "Class" is its own class.
    cls_class = next(o for o, b in objects if q(b, OBJ_CLASS) == o)
    class_info = {}

    def info(cls):
        if cls not in class_info:
            b = g.mem.read(cls, CLASS_CAST_FLAGS + 8)
            class_info[cls] = (g.fname(b, OBJ_NAME), q(b, CLASS_CAST_FLAGS)) if b else ("?", 0)
        return class_info[cls]

    by_name = {}
    for o, b in objects:
        if q(b, OBJ_CLASS) == cls_class:
            by_name.setdefault(g.fname(b, OBJ_NAME), o)
    script_struct_class, enum_class, package_class = by_name["ScriptStruct"], by_name["Enum"], by_name["Package"]

    def path_name(o):
        parts = []
        while o:
            b = base_of.get(o) or g.read(o, 0x28)
            parts.append((o, g.fname(b, OBJ_NAME), q(b, OBJ_CLASS), q(b, OBJ_OUTER)))
            o = q(b, OBJ_OUTER)
        parts.reverse()
        text = parts[0][1]
        for (po, _, pcls, pouter), (_, name, _, _) in zip(parts, parts[1:]):
            outer_of_outer_cls = q(base_of.get(pouter) or g.read(pouter, 0x28), OBJ_CLASS) if pouter else None
            sep = ":" if pcls != package_class and outer_of_outer_cls == package_class else "."
            text += sep + name
        return text

    # Property classes: FFieldClass -> its first known name along SuperClass.
    field_classes = {}

    def field_class_name(fc):
        if fc not in field_classes:
            names, walk = [], fc
            while walk and len(names) < 16:
                b = g.read(walk, 0x50)
                names.append(g.fname(b, FIELDCLASS_NAME))
                walk = q(b, FIELDCLASS_SUPER)
            field_classes[fc] = next((n for n in names if n in SIMPLE or n in CONTAINERS), names[0])
        return field_classes[fc]

    stats = {"null enum (written as the number type)": [], "unknown property type": [], "struct left out": [],
             "null struct": []}

    def owned_field(ptr, owner):
        if not ptr:
            raise Broken("missing inner property")
        fb = g.read(ptr, 0x80)
        if (q(fb, FIELD_OWNER) or 0) & PTR_MASK != owner:
            raise Broken(f"inner property {ptr:#x} is not owned by {owner:#x}")
        return fb

    def obj_name(ptr):
        b = base_of.get(ptr) or g.mem.read(ptr, 0x28)
        if not b:
            raise Broken(f"object {ptr:#x}")
        return g.fname(b, OBJ_NAME)

    def prop_type(ptr, fb, where):
        kind = field_class_name(q(fb, FIELD_CLASS))
        if kind in SIMPLE:
            return (SIMPLE[kind],)
        if kind == "ByteProperty":
            enum = q(fb, PROP_INNER_A)
            return ("Enum", ("Byte",), usmap_name(obj_name(enum))) if enum else ("Byte",)
        if kind == "EnumProperty":
            inner_ptr = q(fb, PROP_INNER_A)
            inner = prop_type(inner_ptr, owned_field(inner_ptr, ptr), where)
            enum = q(fb, PROP_INNER_B)
            if not enum:
                stats["null enum (written as the number type)"].append(where)
                return inner
            return ("Enum", inner, usmap_name(obj_name(enum)))
        if kind == "StructProperty":
            st = q(fb, PROP_INNER_A)
            if not st:
                stats["null struct"].append(where)
                return ("Unknown",)
            return ("Struct", usmap_name(obj_name(st)))
        if kind in ("SetProperty", "OptionalProperty", "ArrayProperty"):
            inner_ptr = q(fb, PROP_INNER_B if kind == "ArrayProperty" else PROP_INNER_A)
            label = {"SetProperty": "Set", "OptionalProperty": "Optional", "ArrayProperty": "Array"}[kind]
            return (label, prop_type(inner_ptr, owned_field(inner_ptr, ptr), where))
        if kind == "MapProperty":
            k, v = q(fb, PROP_INNER_A), q(fb, PROP_INNER_B)
            return ("Map", prop_type(k, owned_field(k, ptr), where), prop_type(v, owned_field(v, ptr), where))
        stats["unknown property type"].append(f"{where} ({kind})")
        return ("Unknown",)

    result = usmap_io.Usmap()
    skipped_cdo = 0
    for o, b in objects:
        cls = q(b, OBJ_CLASS)
        is_struct = cls in (cls_class, script_struct_class)
        is_enum = cls == enum_class
        if not native_only and not is_struct and not is_enum:
            try:
                cast = info(cls)[1] or 0
            except Broken:
                continue
            is_struct = bool(cast & (CASTCLASS_UCLASS | CASTCLASS_USCRIPTSTRUCT))
            is_enum = not is_struct and bool(cast & CASTCLASS_UENUM)
        if not (is_struct or is_enum):
            continue
        if (i32(b, OBJ_FLAGS) or 0) & RF_CDO_OR_ARCHETYPE:
            skipped_cdo += 1
            continue
        name = f"object {o:#x}"
        try:
            name = usmap_name(g.fname(b, OBJ_NAME))
            full = path_name(o)
            path = usmap_name(full[:full.rfind(".")] if "." in full else full)
            cast = info(cls)[1] or 0
            if is_enum:
                eb = g.read(o, 0x60)
                n = i32(eb, ENUM_COUNT)
                if n is None or not 0 <= n < 65536:
                    raise Broken(f"enum count {n}")
                # Some enums (19 engine enums such as EPixelFormat) have bit 0 of both pointers set as a flag.
                raw_names = g.read(q(eb, ENUM_NAMES) & ~3, 8 * n) if n else b""
                raw_values = g.read(q(eb, ENUM_VALUES) & ~3, 8 * n) if n else b""
                entries = [(struct.unpack_from("<q", raw_values, 8 * k)[0], usmap_name(g.fname(raw_names, 8 * k)))
                           for k in range(n)]
                result.enums.append(usmap_io.Enum(name, entries, path, eb[ENUM_FLAGS]))
                continue
            sb = g.read(o, CLASS_CAST_FLAGS + 8 if cast & CASTCLASS_UCLASS else SCRIPTSTRUCT_FLAGS + 8)
            sup = q(sb, STRUCT_SUPER)
            props, index, f = [], 0, q(sb, STRUCT_CHILD_PROPS)
            while f:
                if len(props) > MAX_FIELDS:
                    raise Broken("property chain too long")
                fb = g.read(f, 0x80)
                if (q(fb, FIELD_OWNER) or 0) & PTR_MASK != o:
                    raise Broken(f"property {f:#x} is not owned by the struct")
                pname = usmap_name(g.fname(fb, FIELD_NAME))
                dim = i32(fb, PROP_ARRAY_DIM)
                if dim is None or not 1 <= dim <= 255:
                    raise Broken(f"array size {dim} of {pname}")
                ptype = prop_type(f, fb, f"{name}.{pname}")
                props.append(usmap_io.Prop(index, dim, pname, ptype, q(fb, PROP_FLAGS)))
                index += dim
                f = q(fb, FIELD_NEXT)
            if cast & CASTCLASS_USCRIPTSTRUCT:
                kind, flags = 1, i32(sb, SCRIPTSTRUCT_FLAGS)
            elif cast & CASTCLASS_UCLASS:
                kind, flags = 2, i32(sb, CLASS_FLAGS)
            else:
                kind, flags = 0, 0
            result.structs.append(usmap_io.Struct(name, usmap_name(obj_name(sup)) if sup else None, props, path,
                                                  kind, flags))
        except Broken as e:
            stats["struct left out"].append(f"{name}: {e}")

    usmap_io.write(result, out)
    bp = sum(1 for s in result.structs if s.path and not s.path.startswith("/Script/"))
    bpe = sum(1 for e in result.enums if e.path and not e.path.startswith("/Script/"))
    report = [
        f"{out.name}: {'native types only' if native_only else 'with Blueprint types'}, {time.time() - start:.1f} s",
        f"structs {len(result.structs)} (from assets {bp}), enums {len(result.enums)} (from assets {bpe}), "
        f"properties {sum(len(s.props) for s in result.structs)}, class default objects left out {skipped_cdo}",
    ]
    for label, items in stats.items():
        report.append(f"{label}: {len(items)}")
        report += [f"    {x}" for x in items[:40]]
    text = "\n".join(report)
    print(text)
    out.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
