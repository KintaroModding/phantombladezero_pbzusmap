"""Merges mapping files (.usmap) of one game build into one file with every type once.

Use: a native dump, live dumps (usmap_live.py) and the types read from the packages (usmap_offline.py), so the merged
file holds every type of all of them. A reader looks types up by name, so the result has each name once:
- a native type (path /Script/...) wins over a type from an asset with the same name;
- otherwise the schema seen in the most files wins; on a tie the one with more properties, then the first;
- schemas are compared without case (Unreal names ignore it); conflicts (same name, different schema) are listed.
The first file sets the order; types only in later files follow. Writes usmap version 4 with PPTH and EATR.
A last pass keeps every reference readable: an enum type that no file defines becomes its number type (the game
holds such a property as a plain number), a struct reference without a name gets the name "None" (never the
index 0xFFFFFFFF, which stops some readers), and a type whose super type no file defines is left out (Phantom
Blade Zero: one editor-only widget). The report names each case.

    python -I usmap_merge.py <out.usmap> <in1.usmap> <in2.usmap> [...]
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usmap_io  # noqa: E402


def pick(cands, schema_of, size_of):
    native = [c for c in cands if c.path and c.path.startswith("/Script/")]
    if native:
        return native[0], len({schema_of(c) for c in cands}) > 1
    counts = Counter(schema_of(c) for c in cands)
    best = max(cands, key=lambda c: (counts[schema_of(c)], size_of(c)))
    return best, len(counts) > 1


def main():
    sys.stdout.reconfigure(errors="backslashreplace")  # game names can be Chinese; the console may be cp1252
    out, inputs = Path(sys.argv[1]), sys.argv[2:]
    files = [usmap_io.read(p) for p in inputs]
    merged = usmap_io.Usmap()
    report = []
    for p, u in zip(inputs, files):
        report.append(f"in: {p}: {len(u.structs)} structs, {len(u.enums)} enums")
    # Unreal names ignore case: "Mid" in memory and "MID" in a package are the same property.
    def struct_schema(s):
        return ((s.super or "").lower(),
                tuple((p.index, p.array_dim, p.name.lower(), usmap_io.type_text(p.type).lower()) for p in s.props))

    def enum_schema(e):
        return tuple((value, (text or "").lower()) for value, text in e.entries)

    for kind, attr, schema_of, size_of in (
            ("structs", "structs", struct_schema, lambda s: len(s.props)),
            ("enums", "enums", enum_schema, lambda e: len(e.entries))):
        order, cands = [], {}
        for u in files:
            for item in getattr(u, attr):
                if item.name not in cands:
                    order.append(item.name)
                    cands[item.name] = []
                cands[item.name].append(item)
        conflicts = []
        for name in order:
            best, conflict = pick(cands[name], schema_of, size_of)
            getattr(merged, attr).append(best)
            if conflict:
                paths = sorted({c.path or "no path" for c in cands[name]})
                conflicts.append(f"    {name}: {len(cands[name])} entries, kept {best.path or 'no path'}; paths {paths[:4]}")
        dupes = sum(len(c) for c in cands.values()) - len(order)
        report.append(f"{kind}: {len(order)} names, {dupes} copies merged, {len(conflicts)} with different schemas")
        report += conflicts[:60]
    enum_names = {e.name for e in merged.enums}
    fixes = {"enum not defined -> number type": [], "struct reference without a name -> None": []}

    def fix(t, where):
        if t[0] == "Enum":
            inner = fix(t[1], where)
            if t[2] not in enum_names:
                fixes["enum not defined -> number type"].append(f"{where} ({t[2]})")
                return inner
            return ("Enum", inner, t[2])
        if t[0] == "Struct" and t[1] is None:
            fixes["struct reference without a name -> None"].append(where)
            return ("Struct", "None")
        if t[0] in ("Array", "Set", "Optional"):
            return (t[0], fix(t[1], where))
        if t[0] == "Map":
            return ("Map", fix(t[1], where), fix(t[2], where))
        return t

    for s in merged.structs:
        for p in s.props:
            p.type = fix(p.type, f"{s.name}.{p.name}")
    kept = []
    while True:
        names = {s.name for s in merged.structs}
        kept = [s for s in merged.structs if not s.super or s.super in names]
        dropped = [s for s in merged.structs if s not in kept]
        fixes.setdefault("super type not defined -> left out", [])
        fixes["super type not defined -> left out"] += [f"{s.name} ({s.path}, super {s.super})" for s in dropped]
        merged.structs = kept
        if not dropped:
            break
    for label, items in fixes.items():
        report.append(f"last pass, {label}: {len(items)}")
        report += [f"    {x}" for x in items[:20]]
    usmap_io.write(merged, out)
    report.append(f"out: {out}: {len(merged.structs)} structs, {len(merged.enums)} enums")
    text = "\n".join(report)
    out.with_suffix(".merge.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
