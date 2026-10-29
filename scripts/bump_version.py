"""Raises the version of the mod by one step: the second number (1.0 -> 1.1, 1.9 -> 1.10).

The version is in mod.json ("version": "<major>.<minor>"). A UE4SS mod also writes it into its log line, so the
script also changes the line `local VERSION = "<major>.<minor>"` in src/<folder>/Scripts/main.lua when that file
exists. The rest of both files keeps its layout.

Usage:
    python scripts/bump_version.py           # 1.0 -> 1.1
    python scripts/bump_version.py --major   # 1.4 -> 2.0, only when the user says so
    python scripts/bump_version.py --show    # print the version
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOD_JSON = ROOT / "mod.json"
VERSION = re.compile(r"^(\d+)\.(\d+)$")
JSON_VERSION = re.compile(r'("version"\s*:\s*")([^"]*)(")')
LUA_VERSION = re.compile(r'^(local VERSION = ")([^"]*)(")', re.MULTILINE)


def main() -> None:
    text = MOD_JSON.read_text(encoding="utf-8")
    mod = json.loads(text)
    current = mod["version"]
    match = VERSION.match(current)
    if not match:
        sys.exit(f'mod.json: version "{current}" is not <major>.<minor>')
    lua = ROOT / "src" / mod["folder"] / "Scripts" / "main.lua"
    lua_text = lua.read_text(encoding="utf-8") if lua.is_file() else None
    if lua_text is not None:
        lua_match = LUA_VERSION.search(lua_text)
        if lua_match is None:
            sys.exit(f"{lua}: no line 'local VERSION = \"<major>.<minor>\"'")
        if lua_match.group(2) != current:
            sys.exit(f"Versions differ: mod.json {current}, main.lua {lua_match.group(2)}. Make them equal first.")

    if "--show" in sys.argv:
        print(current)
        return

    major, minor = int(match.group(1)), int(match.group(2))
    new = f"{major + 1}.0" if "--major" in sys.argv else f"{major}.{minor + 1}"
    MOD_JSON.write_text(JSON_VERSION.sub(lambda m: m.group(1) + new + m.group(3), text, count=1),
                        encoding="utf-8", newline="\n")
    if lua_text is not None:
        lua.write_text(LUA_VERSION.sub(lambda m: m.group(1) + new + m.group(3), lua_text, count=1),
                       encoding="utf-8", newline="\n")
    print(f"Version {current} -> {new}")


if __name__ == "__main__":
    main()
