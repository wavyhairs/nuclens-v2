import json, sys

records = json.loads(sys.argv[1])
path = sys.argv[2]
with open(path, "a", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"wrote {len(records)} lines to {path}")
