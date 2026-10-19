"""Report maximum GLIBC symbol requirement across executable ELF objects."""
from __future__ import annotations
import argparse, re, subprocess
from pathlib import Path

PATTERN = re.compile(r"GLIBC_(\d+)\.(\d+)")

def version(path: Path):
    try:
        text = subprocess.check_output(["readelf", "--version-info", str(path)], text=True,
                                       stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return None
    found = [(int(a), int(b)) for a, b in PATTERN.findall(text)]
    return max(found) if found else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ns = ap.parse_args()
    rows = []
    for path in ns.root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.read_bytes()[:4] != b"\x7fELF":
                continue
        except OSError:
            continue
        v = version(path)
        if v:
            rows.append((v, path))
    rows.sort(reverse=True)
    for v, path in rows[:30]:
        print(f"GLIBC_{v[0]}.{v[1]}\t{path}")
    if rows:
        floor, path = rows[0]
        distinct = sorted({v for v, _ in rows}, reverse=True)
        next_tier = distinct[1] if len(distinct) > 1 else None
        print(f"MAX=GLIBC_{floor[0]}.{floor[1]} file={path}")
        print("NEXT=" + (f"GLIBC_{next_tier[0]}.{next_tier[1]}" if next_tier else "none"))
    else:
        raise SystemExit("No ELF objects inspected")
if __name__ == "__main__": main()
