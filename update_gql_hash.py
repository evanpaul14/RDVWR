#!/usr/bin/env python3
"""Find the Reddit Android app's persisted GraphQL query hashes in a jadx-decompiled tree
and write them to gql_hashes.json. Run by .github/workflows/update-gql-hash.yml:
    python3 update_gql_hash.py <jadx output dir>
    python3 update_gql_hash.py classes <apk> <operation>   (class names to decompile)
Exits non-zero (leaving the file alone) when an operation's hash can't be found uniquely."""
import json
import re
import sys
import zipfile
from pathlib import Path

HASHES_FILE = Path(__file__).parent / "gql_hashes.json"
HEX64_RE = re.compile(r'"([0-9a-f]{64})"')


def find_hash(src_dir, operation):
    """The one sha256 literal that sits in a source file next to the exact "<operation>"
    string literal (an Apollo operation class keeps its id and name together)."""
    name_literal = f'"{operation}"'
    found = set()
    for path in Path(src_dir).rglob("*.java"):
        text = path.read_text(errors="ignore")
        if name_literal in text:
            found.update(HEX64_RE.findall(text))
    return found.pop() if len(found) == 1 else None


def find_classes(apk, operation):
    """Dotted names of the classes in an APK's dex files named like the operation (Apollo
    generates `<Operation>Query`). Lets jadx decompile just those instead of the whole app."""
    pattern = re.compile(rb"L([\w/$]*" + re.escape(operation.encode()) + rb"(?:Query)?);")
    names = set()
    with zipfile.ZipFile(apk) as z:
        for entry in z.namelist():
            if re.fullmatch(r"classes\d*\.dex", entry):
                names.update(m.decode().replace("/", ".") for m in pattern.findall(z.read(entry)))
    return sorted(names)


def main(src_dir):
    hashes = json.loads(HASHES_FILE.read_text())
    ok = True
    for operation in hashes:
        new = find_hash(src_dir, operation)
        if not new:
            print(f"{operation}: no unique hash found", file=sys.stderr)
            ok = False
        elif new != hashes[operation]:
            print(f"{operation}: {hashes[operation]} -> {new}")
            hashes[operation] = new
        else:
            print(f"{operation}: unchanged")
    if ok:
        HASHES_FILE.write_text(json.dumps(hashes, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1] == "classes":   # classes <apk> <operation>: dotted class names, one per line
        print("\n".join(find_classes(sys.argv[2], sys.argv[3])))
    else:
        sys.exit(main(sys.argv[1]))
