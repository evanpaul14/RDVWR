#!/usr/bin/env python3
"""Find the Reddit Android app's persisted GraphQL query hashes in its APK and write them to
gql_hashes.json. Run by .github/workflows/update-gql-hash.yml:
    python3 update_gql_hash.py <apk> [<apk> ...]
The app is minified, so class names are useless; instead each Apollo operation class is the
one whose code loads both the operation's name ("PostComments") and its sha256 id as string
constants. The dex files are read directly (no decompiler). Exits non-zero, leaving the file
alone, when an operation's hash can't be found uniquely."""
import json
import re
import struct
import sys
import zipfile
from pathlib import Path

HASHES_FILE = Path(__file__).parent / "gql_hashes.json"
HEX64_RE = re.compile(rb"[0-9a-f]{64}")
DEX_RE = re.compile(r"classes\d*\.dex")
CONST_STRING, CONST_STRING_JUMBO = 0x1A, 0x1B   # dex opcodes that load a string_ids entry


def _uleb(b, i):
    result = shift = 0
    while True:
        x = b[i]
        i += 1
        result |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            return result, i


def _strings(b):
    size, off = struct.unpack_from("<II", b, 0x38)
    out = []
    for k in range(size):
        (data_off,) = struct.unpack_from("<I", b, off + 4 * k)
        _, start = _uleb(b, data_off)   # skip the utf16 length
        out.append(b[start:b.index(b"\0", start)])
    return out


def _class_string_refs(b, wanted):
    """For each class that loads any string index in `wanted`, the set of those indexes."""
    size, off = struct.unpack_from("<II", b, 0x60)
    for k in range(size):
        (class_data_off,) = struct.unpack_from("<I", b, off + 32 * k + 24)
        if not class_data_off:
            continue
        counts, p = [], class_data_off
        for _ in range(4):   # static fields, instance fields, direct methods, virtual methods
            n, p = _uleb(b, p)
            counts.append(n)
        for _ in range(counts[0] + counts[1]):
            _, p = _uleb(b, p)
            _, p = _uleb(b, p)
        refs = set()
        for _ in range(counts[2] + counts[3]):
            _, p = _uleb(b, p)
            _, p = _uleb(b, p)
            code_off, p = _uleb(b, p)
            if not code_off:
                continue
            (n,) = struct.unpack_from("<I", b, code_off + 12)
            units = struct.unpack_from(f"<{n}H", b, code_off + 16)
            for i, u in enumerate(units):
                if u & 0xFF == CONST_STRING and i + 1 < n and units[i + 1] in wanted:
                    refs.add(units[i + 1])
                elif u & 0xFF == CONST_STRING_JUMBO and i + 2 < n:
                    idx = units[i + 1] | units[i + 2] << 16
                    if idx in wanted:
                        refs.add(idx)
        if refs:
            yield refs


def hashes_in_dex(dex, operation):
    """sha256 ids found in classes of one dex file that also load the "<operation>" string."""
    strings = _strings(dex)
    names = {i for i, s in enumerate(strings) if s == operation.encode()}
    hexes = {i for i, s in enumerate(strings) if HEX64_RE.fullmatch(s)}
    if not names or not hexes:
        return set()
    found = set()
    for refs in _class_string_refs(dex, names | hexes):
        if refs & names:
            found.update(strings[i].decode() for i in refs & hexes)
    return found


def find_hash(apks, operation):
    """The one sha256 id that sits in a class with the operation's name, across `apks`."""
    found = set()
    for apk in apks:
        with zipfile.ZipFile(apk) as z:
            for entry in z.namelist():
                if DEX_RE.fullmatch(entry):
                    found |= hashes_in_dex(z.read(entry), operation)
    return found.pop() if len(found) == 1 else None


def main(apks):
    hashes = json.loads(HASHES_FILE.read_text())
    ok = True
    for operation in hashes:
        new = find_hash(apks, operation)
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
    sys.exit(main(sys.argv[1:]))
