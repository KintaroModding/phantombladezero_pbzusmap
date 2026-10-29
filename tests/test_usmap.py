"""Offline tests of the mapping file scripts in src (no game needed).

    python -I tests/test_usmap.py

Checks: usmap_io writes and reads back every type kind, the extensions and a missing super; usmap_offline.py
applies UE4SS's rules (Byte with an enum, an enum property without an enum, '::' names, schema index, sorted
input); usmap_merge.py keeps a native type over an asset type, ignores case, and its last pass fixes undefined
enums, unnamed structs and missing super types.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SRC))
import usmap_io  # noqa: E402
from usmap_io import Enum, Prop, Struct, Usmap  # noqa: E402

failures = []


def check(label, ok):
    print(("ok    " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def run(script, *args):
    result = subprocess.run([sys.executable, "-I", "-B", str(SRC / script), *map(str, args)], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, result.stderr)
    return result.returncode


def test_roundtrip(tmp):
    u = Usmap()
    u.enums.append(Enum("EColor", [(0, "Red"), (5, "Blue")], "/Script/Test", 1))
    u.structs.append(Struct("Base", None, [Prop(0, 1, "Id", ("Int",), 0x20)], "/Script/Test", 2, 0x80))
    types = [("Struct", "Base"), ("Enum", ("Byte",), "EColor"), ("Array", ("Name",)), ("Set", ("Str",)),
             ("Optional", ("Double",)), ("Map", ("Name",), ("Struct", "Base")), ("Bool",), ("Unknown",)]
    props = [Prop(i, 1, f"P{i}", t, i) for i, t in enumerate(types)]
    props.append(Prop(len(types), 3, "Fixed", ("Float",)))
    u.structs.append(Struct("Child_C", "Base", props, "/Game/Child", 2, 0x40000))
    for ext in (True, False):
        path = tmp / f"roundtrip_{ext}.usmap"
        usmap_io.write(u, path, extensions=ext)
        back = usmap_io.read(path)
        same = [a.schema() for a in u.structs] == [b.schema() for b in back.structs]
        same = same and [(e.name, e.entries) for e in u.enums] == [(e.name, e.entries) for e in back.enums]
        check(f"roundtrip, extensions {ext}: structs and enums equal", same)
        if ext:
            check("roundtrip: PPTH paths and EATR flags equal",
                  [(s.path, s.kind, s.flags, [p.flags for p in s.props]) for s in back.structs]
                  == [(s.path, s.kind, s.flags, [p.flags for p in s.props]) for s in u.structs]
                  and [(e.path, e.flags) for e in back.enums] == [(e.path, e.flags) for e in u.enums])
        else:
            check("roundtrip without extensions: no extensions read", back.extensions == [] and back.null_names == 0)
    check("type_text", usmap_io.type_text(("Map", ("Name",), ("Enum", ("Byte",), "EColor")))
          == "Map<Name, Enum<EColor, Byte>>")


def test_offline(tmp):
    rows = [
        {"pkg": "/Game/B", "name": "B_C", "cls": "BlueprintGeneratedClass", "objflags": 1, "kind": 2, "flags": 0x40000,
         "super": "Actor", "props": [
             {"n": "Mode", "d": 1, "f": 4, "t": {"k": "ByteProperty", "enum": "E_Mode"}},
             {"n": "Raw", "d": 2, "f": 0, "t": {"k": "ByteProperty", "enum": None}},
             {"n": "NoEnum", "d": 1, "f": 0, "t": {"k": "EnumProperty", "under": {"k": "ByteProperty"}, "enum": None}},
             {"n": "Target", "d": 1, "f": 0, "t": {"k": "ClassProperty"}},
             {"n": "List", "d": 1, "f": 0, "t": {"k": "ArrayProperty", "inner": {"k": "StructProperty", "struct": "S_Row"}}},
         ]},
        {"pkg": "/Game/A", "name": "E_Mode", "cls": "UserDefinedEnum", "objflags": 1, "kind": 3,
         "entries": [[0, "E_Mode::NewEnumerator0"], [1, "E_Mode::E_MAX"]]},
        {"pkg": "/Game/A", "name": "Default__X_C", "cls": "X_C", "objflags": 0x10, "kind": 2, "flags": 0, "super": None,
         "props": []},
    ]
    src = tmp / "types.jsonl"
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp / "types.usmap"
    check("usmap_offline.py runs", run("usmap_offline.py", src, out) == 0)
    u = usmap_io.read(out)
    check("class default object left out, one class and one enum", len(u.structs) == 1 and len(u.enums) == 1)
    s = u.structs[0]
    types = {p.name: (p.index, p.array_dim, p.type) for p in s.props}
    check("ByteProperty with an enum is Enum<Byte>", types["Mode"] == (0, 1, ("Enum", ("Byte",), "E_Mode")))
    check("ByteProperty without an enum is Byte, array size 2", types["Raw"] == (1, 2, ("Byte",)))
    check("schema index is the running sum of ArrayDim", types["NoEnum"][0] == 3)
    check("EnumProperty without an enum is its number type", types["NoEnum"][2] == ("Byte",))
    check("ClassProperty is Object", types["Target"][2] == ("Object",))
    check("Array of Struct", types["List"][2] == ("Array", ("Struct", "S_Row")))
    check("enum entries lose the 'E_Mode::' part", u.enums[0].entries == [(0, "NewEnumerator0"), (1, "E_MAX")])
    check("PPTH is the package name, EATR the class flags", s.path == "/Game/B" and s.kind == 2 and s.flags == 0x40000)


def test_merge(tmp):
    native = Usmap()
    native.enums.append(Enum("ETeleportType", [(0, "None")], "/Script/Engine"))
    native.structs.append(Struct("Actor", "Object", [], "/Script/Engine", 2))
    native.structs.append(Struct("Object", None, [], "/Script/CoreUObject", 2))
    assets = Usmap()
    assets.enums.append(Enum("ETeleportType", [(0, "A"), (1, "B")], "/Game/ETeleportType"))
    assets.structs.append(Struct("Actor", "Object", [Prop(0, 1, "Fake", ("Int",))], "/Game/Actor", 2))
    assets.structs.append(Struct("W_C", "Actor", [Prop(0, 1, "Mid", ("Object",))], "/Game/W", 2))
    assets.structs.append(Struct("Cond_C", "Actor", [
        Prop(0, 1, "Method", ("Enum", ("Byte",), "EMissing")),
        Prop(1, 1, "Data", ("Map", ("Name",), ("Struct", None)))], "/Game/Cond", 2))
    assets.structs.append(Struct("EditorOnly_C", "EditorWidget", [], "/Game/Editor", 2))
    live = Usmap()
    live.structs.append(Struct("W_C", "Actor", [Prop(0, 1, "MID", ("Object",), 0x8)], "/Game/W", 2))
    for name, u in (("native", native), ("live", live), ("assets", assets)):
        usmap_io.write(u, tmp / f"{name}.usmap")
    out = tmp / "merged.usmap"
    check("usmap_merge.py runs", run("usmap_merge.py", out, tmp / "native.usmap", tmp / "live.usmap",
                                     tmp / "assets.usmap") == 0)
    m = usmap_io.read(out)
    s = {x.name: x for x in m.structs}
    e = {x.name: x for x in m.enums}
    check("a native type wins over an asset type of the same name",
          s["Actor"].path == "/Script/Engine" and e["ETeleportType"].path == "/Script/Engine")
    check("case-only differences are one schema; the first file wins", s["W_C"].props[0].name == "MID")
    check("undefined enum -> number type", s["Cond_C"].props[0].type == ("Byte",))
    check("struct reference without a name -> None", s["Cond_C"].props[1].type == ("Map", ("Name",), ("Struct", "None")))
    check("type with an undefined super type left out", "EditorOnly_C" not in s)
    check("each name once", len(m.structs) == len(s) and m.null_names == 0)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        test_roundtrip(tmp)
        test_offline(tmp)
        test_merge(tmp)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
