"""Reads and writes mapping files (.usmap) with their extensions; shared by the other scripts in this folder.

Format: usmap version 4 (ExplicitEnumValues) without compression and without package versioning, as UE4SS writes
it (UE4SS/src/USMapGenerator/Generator.cpp, commit cdbc0b66), with the CEXT block and the extensions PPTH (package
path of each enum and struct) and EATR (enum flags, struct or class flags, property flags). Reads versions 0 to 4.
A name index 0xFFFFFFFF where a name must be (for example an enum property written without its enum) reads as None
and is counted in Usmap.null_names.

Types are tuples: ("Byte",), ("Struct", name), ("Enum", inner, enum name or None), ("Array", inner),
("Set", inner), ("Optional", inner), ("Map", key, value).
"""
import struct
from dataclasses import dataclass, field

TYPES = [
    "Byte", "Bool", "Int", "Float", "Object", "Name", "Delegate", "Double", "Array", "Struct", "Str", "Text",
    "Interface", "MulticastDelegate", "WeakObject", "LazyObject", "AssetObject", "SoftObject", "UInt64", "UInt32",
    "UInt16", "Int64", "Int16", "Int8", "Map", "Set", "Enum", "FieldPath", "Optional", "Utf8Str", "AnsiStr",
]
TYPE_ID = {name: i for i, name in enumerate(TYPES)}
UNKNOWN = 0xFF
NO_NAME = 0xFFFFFFFF

# Unreal property class names -> usmap types without inner data, as UE4SS's GetPropertyType maps them by cast flags.
# ByteProperty, EnumProperty, StructProperty and the containers carry inner data; the readers handle them.
SIMPLE = {
    "BoolProperty": "Bool", "IntProperty": "Int", "FloatProperty": "Float", "NameProperty": "Name",
    "DelegateProperty": "Delegate", "DoubleProperty": "Double", "StrProperty": "Str", "TextProperty": "Text",
    "InterfaceProperty": "Interface", "MulticastDelegateProperty": "MulticastDelegate",
    "MulticastInlineDelegateProperty": "MulticastDelegate", "MulticastSparseDelegateProperty": "MulticastDelegate",
    "WeakObjectProperty": "WeakObject", "LazyObjectProperty": "LazyObject", "SoftObjectProperty": "SoftObject",
    "SoftClassProperty": "SoftObject", "UInt64Property": "UInt64", "UInt32Property": "UInt32",
    "UInt16Property": "UInt16", "Int64Property": "Int64", "Int16Property": "Int16", "Int8Property": "Int8",
    "FieldPathProperty": "FieldPath", "Utf8StrProperty": "Utf8Str", "AnsiStrProperty": "AnsiStr",
    "ObjectProperty": "Object", "ClassProperty": "Object", "ObjectPtrProperty": "Object", "ClassPtrProperty": "Object",
}
CONTAINERS = {"ByteProperty", "EnumProperty", "StructProperty", "ArrayProperty", "SetProperty", "MapProperty",
              "OptionalProperty"}


def usmap_name(text):
    """UE4SS writes each name from its first '::' on without that part (enum entries 'EFoo::Bar' -> 'Bar')."""
    at = text.find("::")
    return text if at < 0 else text[at + 2:]


@dataclass
class Prop:
    index: int
    array_dim: int
    name: str
    type: tuple
    flags: int = 0  # EATR property flags


@dataclass
class Struct:
    name: str
    super: str | None
    props: list
    path: str | None = None
    kind: int = 0  # EATR: 1 script struct, 2 class, 0 unknown
    flags: int = 0  # EATR: struct flags or class flags

    def schema(self):
        return (self.super, tuple((p.index, p.array_dim, p.name, p.type) for p in self.props))


@dataclass
class Enum:
    name: str
    entries: list  # (value, name)
    path: str | None = None
    flags: int = 0


@dataclass
class Usmap:
    version: int = 4
    enums: list = field(default_factory=list)
    structs: list = field(default_factory=list)
    extensions: list = field(default_factory=list)
    null_names: int = 0  # name indexes 0xFFFFFFFF seen while reading
    size: int = 0


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, fmt):
        values = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return values[0] if len(values) == 1 else values

    def bytes(self, n):
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out


def type_text(t):
    if t[0] == "Struct":
        return f"Struct<{t[1]}>"
    if t[0] == "Enum":
        return f"Enum<{t[2]}, {type_text(t[1])}>"
    if t[0] == "Map":
        return f"Map<{type_text(t[1])}, {type_text(t[2])}>"
    if t[0] in ("Array", "Set", "Optional"):
        return f"{t[0]}<{type_text(t[1])}>"
    return t[0]


def read(path):
    with open(path, "rb") as f:
        raw = f.read()
    r = _Reader(raw)
    if r.take("H") != 0x30C4:
        raise ValueError(f"{path}: not a usmap file")
    u = Usmap(version=r.take("B"), size=len(raw))
    if u.version > 4:
        raise ValueError(f"{path}: usmap version {u.version} is unknown")
    if u.version >= 1 and r.take("i") > 0:
        r.take("ii")
        for _ in range(r.take("I")):
            r.bytes(20)
        r.take("i")
    compression = r.take("B")
    compressed, _ = r.take("II")
    if compression != 0:
        raise ValueError(f"{path}: compression method {compression} is not supported")
    r = _Reader(r.bytes(compressed))
    name_len = "H" if u.version >= 2 else "B"
    names = [r.bytes(r.take(name_len)).decode("utf-8", "replace") for _ in range(r.take("I"))]

    def name(may_be_none=False):
        i = r.take("I")
        if i == NO_NAME or i >= len(names):
            if not may_be_none:
                u.null_names += 1
            return None
        return names[i]

    def prop_type():
        kind = r.take("B")
        label = TYPES[kind] if kind < len(TYPES) else "Unknown"
        if label in ("Array", "Set", "Optional"):
            return (label, prop_type())
        if label == "Map":
            key = prop_type()
            return ("Map", key, prop_type())
        if label == "Struct":
            return ("Struct", name())
        if label == "Enum":
            inner = prop_type()
            return ("Enum", inner, name())
        return (label,)

    for _ in range(r.take("I")):
        enum_name = name()
        count = r.take("H") if u.version >= 3 else r.take("B")
        entries = []
        for i in range(count):
            value = r.take("q") if u.version >= 4 else i
            entries.append((value, name()))
        u.enums.append(Enum(enum_name, entries))
    for _ in range(r.take("I")):
        struct_name = name()
        super_name = name(may_be_none=True)
        r.take("H")
        props = []
        for _ in range(r.take("H")):
            index, array_dim = r.take("HB")
            props.append(Prop(index, array_dim, name(), prop_type()))
        u.structs.append(Struct(struct_name, super_name, props))

    while r.pos < len(r.data):
        tag = r.bytes(4).decode("ascii", "replace")
        u.extensions.append(tag)
        if tag == "CEXT":
            r.take("BI")
            continue
        size = r.take("I")
        end = r.pos + size
        if tag == "PPTH":
            r.take("B")
            paths = [name() for _ in range(r.take("I"))]
            for e, p in zip(u.enums, paths):
                e.path = p
            paths = [name() for _ in range(r.take("I"))]
            for s, p in zip(u.structs, paths):
                s.path = p
        elif tag == "EATR":
            r.take("B")
            for e in u.enums[:r.take("I")]:
                e.flags = r.take("i")
            for s in u.structs[:r.take("I")]:
                s.kind, s.flags = r.take("Bi")
                flags = [r.take("Q") for _ in range(r.take("I"))]
                for p, fl in zip(s.props, flags):
                    p.flags = fl
        r.pos = end
    return u


def write(u, path, extensions=True):
    """Writes u as usmap version 4 with CEXT, PPTH and EATR, like UE4SS (extensions=False: without them)."""
    names, order = {}, []

    def nid(text):
        if text is None:
            return NO_NAME
        if text not in names:
            names[text] = len(order)
            order.append(text)
        return names[text]

    body = bytearray()

    def put(fmt, *values):
        body.extend(struct.pack("<" + fmt, *values))

    def put_type(t):
        label = t[0]
        put("B", TYPE_ID.get(label, UNKNOWN))
        if label in ("Array", "Set", "Optional"):
            put_type(t[1])
        elif label == "Map":
            put_type(t[1])
            put_type(t[2])
        elif label == "Struct":
            put("I", nid(t[1]))
        elif label == "Enum":
            put_type(t[1])
            put("I", nid(t[2]))

    put("I", len(u.enums))
    for e in u.enums:
        put("IH", nid(e.name), len(e.entries))
        for value, entry in e.entries:
            put("qI", value, nid(entry))
    put("I", len(u.structs))
    for s in u.structs:
        put("II", nid(s.name), nid(s.super))
        put("HH", sum(p.array_dim for p in s.props), len(s.props))
        for p in s.props:
            put("HBI", p.index, p.array_dim, nid(p.name))
            put_type(p.type)

    if extensions:
        put("4sBI", b"CEXT", 0, 2)
        ext = bytearray()
        ext += struct.pack("<BI", 0, len(u.enums))
        ext += b"".join(struct.pack("<I", nid(e.path)) for e in u.enums)
        ext += struct.pack("<I", len(u.structs))
        ext += b"".join(struct.pack("<I", nid(s.path)) for s in u.structs)
        put("4sI", b"PPTH", len(ext))
        body.extend(ext)
        ext = bytearray()
        ext += struct.pack("<BI", 0, len(u.enums))
        ext += b"".join(struct.pack("<i", e.flags) for e in u.enums)
        ext += struct.pack("<I", len(u.structs))
        for s in u.structs:
            ext += struct.pack("<BiI", s.kind, s.flags, len(s.props))
            ext += b"".join(struct.pack("<Q", p.flags) for p in s.props)
        put("4sI", b"EATR", len(ext))
        body.extend(ext)

    head = bytearray(struct.pack("<I", len(order)))
    for text in order:
        raw = text.encode("utf-8")
        head += struct.pack("<H", len(raw)) + raw
    data = bytes(head) + bytes(body)
    with open(path, "wb") as f:
        f.write(struct.pack("<HBiBII", 0x30C4, 4, 0, 0, len(data), len(data)))
        f.write(data)
