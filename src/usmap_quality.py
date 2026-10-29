"""Measures the quality of mapping files (.usmap) of one game, side by side.

For each file: size, counts, native and asset types (by PPTH paths; a file without them borrows the paths of the
other files), integrity (name indexes that point nowhere, struct, enum and super types used but not defined, names
defined twice, unknown property types), coverage of the game's Blueprint classes (package <name> -> class
<name>_C, from a file list such as `retoc list` output), and agreement with the first file on the structs both hold.

    python -I usmap_quality.py <file list.txt> <a.usmap> <b.usmap> [...]
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usmap_io  # noqa: E402

PREFIXES = ("BP", "GA", "GE", "GC", "ABP", "W", "WBP", "BPI", "BPF", "AN", "ANS", "PDA")


def refs(t, structs, enums):
    if t[0] == "Struct":
        structs.add(t[1])
    elif t[0] == "Enum":
        enums.add(t[2])
        refs(t[1], structs, enums)
    elif t[0] in ("Array", "Set", "Optional"):
        refs(t[1], structs, enums)
    elif t[0] == "Map":
        refs(t[1], structs, enums)
        refs(t[2], structs, enums)


def unknown_types(t):
    if t[0] == "Unknown":
        return 1
    return sum(unknown_types(x) for x in t[1:] if isinstance(x, tuple))


def measure(u, packages, known_paths, first):
    names = {s.name for s in u.structs}
    enum_names = {e.name for e in u.enums}

    def native(item, key):
        return (item.path or known_paths.get(key) or "").startswith("/Script/")

    nat_s = sum(1 for s in u.structs if native(s, s.name))
    nat_e = sum(1 for e in u.enums if native(e, "enum:" + e.name))
    asset_s = [s for s in u.structs if not native(s, s.name)]
    used_s, used_e, unknown = set(), set(), 0
    for s in u.structs:
        for p in s.props:
            refs(p.type, used_s, used_e)
            unknown += unknown_types(p.type)
    used_s.discard(None)
    used_e.discard(None)
    shared = [s for s in u.structs if s.name in first]
    m = {
        "size (bytes)": u.size,
        "extensions": ",".join(x for x in u.extensions if x != "CEXT") or "none",
        "structs": len(u.structs),
        "  native (/Script/)": nat_s,
        "  from assets": len(asset_s),
        "    Blueprint classes (_C)": sum(1 for s in asset_s if s.name.endswith("_C")),
        "    other (user defined structs, ...)": sum(1 for s in asset_s if not s.name.endswith("_C")),
        "properties": sum(len(s.props) for s in u.structs),
        "enums": len(u.enums),
        "  native enums": nat_e,
        "  enums from assets": len(u.enums) - nat_e,
        "! name indexes to nothing": u.null_names,
        "! struct types used, not defined": len(used_s - names),
        "! enum types used, not defined": len(used_e - enum_names),
        "! super types not defined": len({s.super for s in u.structs if s.super} - names),
        "! struct names twice": sum(c - 1 for c in Counter(s.name for s in u.structs).values()),
        "! enum names twice": sum(c - 1 for c in Counter(e.name for e in u.enums).values()),
        "! unknown property types": unknown,
        "! types without a path (PPTH)": sum(1 for s in u.structs if not s.path) + sum(1 for e in u.enums if not e.path),
    }
    for p in PREFIXES:
        pk = [x for x in packages if x.startswith(p + "_")]
        m[f"% {p}_ packages with their class"] = f"{sum(1 for x in pk if x + '_C' in names)}/{len(pk)}"
    m["% all packages with their class"] = sum(1 for x in packages if x + "_C" in names)
    m["= structs shared with file 1"] = len(shared)
    m["= of these, same schema"] = sum(1 for s in shared if s.schema() == first[s.name])
    return m


def main():
    sys.stdout.reconfigure(errors="backslashreplace")  # game names can be Chinese; the console may be cp1252
    file_list, paths = sys.argv[1], sys.argv[2:]
    files = [usmap_io.read(p) for p in paths]
    packages = set()
    for line in open(file_list, encoding="utf-8", errors="replace"):
        found = re.search(r"/([^/\s]+)\.uasset\s*$", line)
        if found:
            packages.add(found.group(1))
    known_paths = {}
    for u in files:
        for s in u.structs:
            if s.path:
                known_paths.setdefault(s.name, s.path)
        for e in u.enums:
            if e.path:
                known_paths.setdefault("enum:" + e.name, e.path)
    first = {s.name: s.schema() for s in files[0].structs}
    results = [measure(u, packages, known_paths, first) for u in files]
    width = max(len(k) for k in results[0]) + 2
    print("files: " + "; ".join(f"{i + 1} = {p}" for i, p in enumerate(paths)))
    print("".ljust(width) + "".join(f"{'file ' + str(i + 1):>14}" for i in range(len(files))))
    for key in results[0]:
        print(key.ljust(width) + "".join(f"{str(r[key]):>14}" for r in results))


if __name__ == "__main__":
    main()
