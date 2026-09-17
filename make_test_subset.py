import json
from pathlib import Path

src_dir = Path("monday_exports")
dst_dir = Path("monday_exports_test")
dst_dir.mkdir(exist_ok=True)

for name in [
    "open.json",
    "close.json",
    "cancelled.json",
    "duplicate_of_close.json",
]:
    src = src_dir / name
    if not src.exists():
        print(f"Skipping missing file: {src}")
        continue

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    data["items"] = items[:1]
    data["count"] = len(data["items"])

    dst = dst_dir / name
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {dst} with {len(data['items'])} item(s)")