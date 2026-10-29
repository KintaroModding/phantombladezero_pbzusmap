"""Turns the JSON lines of the package reader (usmap_offline\\, C# on CUE4Parse) into a mapping file (.usmap) with
the game's Blueprint types, by the same rules as UE4SS's dump and usmap_live.py:
- a ByteProperty with an enum is Enum<Byte>; an EnumProperty without an enum is its number type;
- names from their first '::' on lose that part (enum entries 'E_Foo::NewEnumerator0' -> 'NewEnumerator0');
- schema index = the running sum of ArrayDim; PPTH = the package name; EATR = the class or struct flags and the
  property flags (enum flags are not in the packages: 0).
The output holds only the types from the packages; merge it with a native dump (usmap_merge.py) for a full file.
The reader writes its lines in no fixed order (it reads in parallel); they are sorted here, so the same packages
always give the same file.

    python -I usmap_offline.py <in.jsonl> <out.usmap>
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usmap_io  # noqa: E402
from usmap_io import SIMPLE, usmap_name  # noqa: E402

problems = Counter()
examples = {}


def note(kind, where):
    problems[kind] += 1
    examples.setdefault(kind, where)


def name_of(text, where):
    if text is None or text.startswith("?"):
        note("unresolved reference", where)
        return None
    return usmap_name(text)


def convert(t, where):
    k = t["k"]
    if k in SIMPLE:
        return (SIMPLE[k],)
    if k == "ByteProperty":
        return ("Enum", ("Byte",), name_of(t["enum"], where)) if t.get("enum") else ("Byte",)
    if k == "EnumProperty":
        inner = convert(t["under"], where)
        if not t.get("enum"):
            note("enum property without an enum (written as its number type)", where)
            return inner
        return ("Enum", inner, name_of(t["enum"], where))
    if k == "StructProperty":
        return ("Struct", name_of(t.get("struct"), where))
    if k in ("ArrayProperty", "SetProperty", "OptionalProperty"):
        return ({"ArrayProperty": "Array", "SetProperty": "Set", "OptionalProperty": "Optional"}[k],
                convert(t["inner"], where))
    if k == "MapProperty":
        return ("Map", convert(t["key"], where), convert(t["value"], where))
    note(f"unknown property type {k}", where)
    return ("Unknown",)


def main():
    sys.stdout.reconfigure(errors="backslashreplace")  # game names can be Chinese; the console may be cp1252
    src, out = sys.argv[1], Path(sys.argv[2])
    u = usmap_io.Usmap()
    rows = [json.loads(line) for line in open(src, encoding="utf-8")]
    rows.sort(key=lambda r: (r["pkg"], r["name"], r["cls"]))
    for row in rows:
        where = f"{row['pkg']}.{row['name']}"
        if row["objflags"] & 0x30:  # class default object or archetype
            continue
        if row["kind"] == 3:
            entries = [(value, usmap_name(text)) for value, text in row["entries"]]
            u.enums.append(usmap_io.Enum(usmap_name(row["name"]), entries, row["pkg"], 0))
            continue
        props, index = [], 0
        for p in row["props"]:
            if "d" not in p:
                note("field that is not a property", where)
                continue
            props.append(usmap_io.Prop(index, p["d"], usmap_name(p["n"]), convert(p["t"], f"{where}.{p['n']}"), p["f"]))
            index += p["d"]
        sup = name_of(row["super"], where) if row.get("super") else None
        u.structs.append(usmap_io.Struct(usmap_name(row["name"]), sup, props, row["pkg"],
                                         1 if row["kind"] == 1 else 2, row["flags"] & 0xFFFFFFFF))
    usmap_io.write(u, out)
    report = [f"{out.name}: {len(u.structs)} structs and classes, {len(u.enums)} enums, "
              f"{sum(len(s.props) for s in u.structs)} properties"]
    for kind, count in problems.most_common():
        report.append(f"{kind}: {count} (first: {examples[kind]})")
    text = "\n".join(report)
    print(text)
    out.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
