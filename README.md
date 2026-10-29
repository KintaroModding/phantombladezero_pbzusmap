# USMAP File (Proper)

A full mapping file (`.usmap`) for Phantom Blade Zero, and the code that makes it. The player and modder page is
`openmods.md`; this file is for developers.

## The scripts (`src\`)

| File | What it does |
|---|---|
| `usmap_io.py` | Reads and writes mapping files: usmap version 4 with the CEXT block and the PPTH and EATR extensions, as UE4SS writes them; reads versions 0 to 4. The usmap type names of Unreal's property classes |
| `process_memory.py` | Read-only access to a running process (find it, find its exe module, read memory) |
| `usmap_live.py` | A mapping file from the running game, read from outside: the same objects, types and rules as UE4SS's mapping generator, with the Blueprint types that are loaded at that moment. `--native-only`: native types only. Holds Phantom Blade Zero's engine layout and the addresses of Steam build 26266538 |
| `usmap_offline\` | C# console tool on CUE4Parse: reads every class, struct and enum export of all packages and writes one JSON line per type. `--probe <text>` lists the exports of matching packages; `--readtest <step>` reads every export of a sample of gameplay packages with a given mapping file |
| `usmap_offline.py` | Turns those JSON lines into a mapping file by UE4SS's rules (sorted, so a build gives the same bytes each time) |
| `usmap_merge.py` | Merges mapping files into one with each name once (native first, then the most common schema; names compared without case) and a last pass that keeps every reference readable; writes a `.merge.txt` report |
| `usmap_quality.py` | Measures mapping files side by side: counts, integrity, Blueprint coverage against a file list, agreement |

## Build

`build.ps1` runs the whole chain: it builds `src\usmap_offline` (`dotnet build`), reads the packages into
`dist\work\packages.jsonl`, converts them (`dist\work\packages.usmap`), merges the native dump, the live dumps and
the package types into `dist\ProjectPBZ-5.7.4-301524-full.usmap`, and writes `dist\phantombladezero_pbzusmap_<version>.zip`
and `dist\SHA256SUMS.txt`.

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -PaksDir <game>\ProjectPBZ\Content\Paks -AesKey <key> -Native <native.usmap> -Oodle <oo2core_9_win64.dll> [-Live <dump.usmap>, ...] [-Dotnet <dotnet.exe>]
```

- Needs: Windows PowerShell 5.1, Python 3.11 or newer (`python` on the PATH, or the venv of a shared `_tools` folder),
  the .NET 10 SDK (CUE4Parse 1.2.2.202610 targets net10.0), and `oo2core_9_win64.dll` (Oodle; the retoc release
  has it).
- The native dump: `python -I src\usmap_live.py <path of the running PBZ.exe> native.usmap --native-only`, or UE4SS's
  Ctrl+Num6 with `DumpUSMAP(false)`. Live dumps (`usmap_live.py` without `--native-only`) are optional: they add the
  runtime flags of the loaded types and the transient types that exist only in memory.
- In the modding base folder of the author, the defaults come from the shared `_tools` folder (`$env:UE5_TOOLS`
  overrides it): the game's path and AES key from `_tools\gamedata\phantombladezero\game.json`, the dumps from
  `_tools\gamedata\phantombladezero\usmap_live\`, Oodle from `_tools\retoc\`, .NET from `_tools\dotnet10\`.
- Each step can also run alone:
  ```powershell
  usmap_offline.exe <Paks> <AES key> <native.usmap> <oo2core_9_win64.dll> <out.jsonl>
  python -I src\usmap_offline.py <out.jsonl> <packages.usmap>
  python -I src\usmap_merge.py <full.usmap> <native.usmap> [<live.usmap> ...] <packages.usmap>
  python -I src\usmap_quality.py <file list> <a.usmap> <b.usmap> ...
  ```
  The file list of `usmap_quality.py` is `retoc list --path` output of the game's container.

## Tests

`python -I tests\test_usmap.py`: offline tests of `usmap_io.py`, `usmap_offline.py` and `usmap_merge.py` (no game
needed).

## Notes

- The game's engine is changed against UE 5.7: UStruct has 0x50 more bytes after ChildProperties, FField has Next
  and Owner swapped, and the FFieldClass name is at 0x30. `usmap_live.py` reads with that layout, the same one as
  UE4SS's custom config for the game ([UE4SS-RE/RE-UE4SS#1484](https://github.com/UE4SS-RE/RE-UE4SS/pull/1484)).
- UE4SS's mapping dump with Blueprint types crashes this game on enum properties without an enum
  ([UE4SS-RE/RE-UE4SS#1483](https://github.com/UE4SS-RE/RE-UE4SS/issues/1483)). These properties are written as their
  number type here.
- UAssetGUI: run it one at a time when you test mapping files. Eight parallel runs gave wrong results (640 exports
  not read of 2,651, against 8 one at a time).

## License

MIT, see `LICENSE`. CUE4Parse (Apache-2.0) comes as a NuGet package when you build; it is not part of this
repository.
