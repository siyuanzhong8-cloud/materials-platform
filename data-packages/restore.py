#!/usr/bin/env python3
"""Rebuild data/*.sqlite from the split xz parts in this folder."""
import hashlib, json, lzma, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "restored")

def sha256(path, buf=1024*1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def main():
    man = json.load(open(os.path.join(HERE, "manifest.json"), encoding="utf-8"))
    os.makedirs(DEST, exist_ok=True)
    for item in man["files"]:
        name = item["name"]
        parts = item["parts"]
        out = os.path.join(DEST, name)
        if name.endswith(".sqlite"):
            decompressor = lzma.LZMADecompressor()
            with open(out, "wb") as outf:
                for part in parts:
                    with open(os.path.join(HERE, part), "rb") as inf:
                        while True:
                            chunk = inf.read(8 * 1024 * 1024)
                            if not chunk:
                                break
                            outf.write(decompressor.decompress(chunk))
                    print("read", part)
            digest = sha256(out)
            if digest != item["sha256"] or os.path.getsize(out) != item["bytes"]:
                raise SystemExit(f"checksum failed for {name}: {digest}")
            print("OK", name, item["bytes"])
        else:
            # already a plain copy
            if not os.path.exists(os.path.join(DEST, name)):
                import shutil
                shutil.copy2(os.path.join(HERE, parts[0]), out)
            print("OK", name)
    print("All files restored into", DEST)

if __name__ == "__main__":
    main()
