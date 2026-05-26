import os, json

base = r"C:\Users\misak\Desktop\zlb-scraper\data\gi"
index = json.load(open(os.path.join(base, "index.json"), encoding="utf-8"))
ndkl = [e for e in index if "挪德卡莱" in e.get("relativePath", "") and e.get("type") == "地图文本"]

print(f"Total map text docs for 挪德卡莱: {len(ndkl)}")
for c in ndkl[:5]:
    path = os.path.join(base, "docs", c["relativePath"])
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            content = f.read()
        print(f"\n--- {c['name']} ({len(content)} chars) ---")
        print(content[:400])
