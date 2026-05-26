"""Extract character persona summaries from game docs."""
import json, os, re

DATA = r"C:\Users\misak\Desktop\zlb-scraper\data"
OUTPUT = r"C:\Users\misak\Desktop\zlb-scraper\character_personas.json"

def extract_gi_persona(content, name):
    parts = []

    # Try to find role description from "更多描述" section
    for m in re.finditer(r'▌.+?·\s*\S+\n(.+?)\n=+', content, re.DOTALL):
        parts.append(m.group(1).strip())

    # Try to find personality from character story
    story_match = re.search(r'### 角色详细\n+(.+?)(?:\n###|\n##|\Z)', content, re.DOTALL)
    if story_match:
        story_text = story_match.group(1)[:300]
        parts.insert(0, story_text)

    # Fallback: extract first meaningful paragraph
    if not parts:
        paras = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 30 and not p.startswith('#')]
        if paras:
            parts.append(paras[0][:200])

    return ' '.join(parts)[:300] if parts else ''


def extract_hsr_persona(content, name):
    parts = []

    # Extract 简介 (bio) - the best personality summary
    bio_match = re.search(r'## 简介\n+(.+?)(?:\n##|\n---|\Z)', content, re.DOTALL)
    if bio_match:
        parts.append(bio_match.group(1).strip()[:200])

    # Extract faction
    faction_match = re.search(r'\*\*阵营:\*\*\s*(.+)', content)
    faction = faction_match.group(1).strip() if faction_match else ''

    # Extract first story for voice samples
    story_match = re.search(r'### 故事 1\n+(.+?)(?:\n###|\Z)', content, re.DOTALL)
    if story_match:
        story = story_match.group(1).strip()[:300]
        parts.append(story)

    if faction:
        parts.insert(0, f"所属: {faction}")

    return ' | '.join(parts)[:400]


def build():
    result = {}

    for domain in ["gi", "hsr"]:
        index_path = os.path.join(DATA, domain, "index.json")
        with open(index_path, encoding="utf-8") as f:
            entries = json.load(f)

        personas = {}

        extract_fn = extract_gi_persona if domain == "gi" else extract_hsr_persona
        char_type = "角色" if domain == "gi" else "Characters"

        for e in entries:
            if e.get("type") != char_type:
                continue

            name = e["name"].strip()
            doc_path = os.path.join(DATA, domain, "docs", e["relativePath"])
            if not os.path.exists(doc_path):
                continue

            with open(doc_path, encoding="utf-8") as f:
                content = f.read()

            persona = extract_fn(content, name)
            if persona:
                personas[name] = persona

        result[domain] = personas
        print(f"{domain}: {len(personas)} character personas extracted")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nWritten to {OUTPUT}")

    # Show samples
    for domain in ["gi", "hsr"]:
        print(f"\n=== {domain} samples ===")
        for name in list(result[domain].keys())[:3]:
            print(f"  {name}: {result[domain][name][:120]}...")


if __name__ == "__main__":
    build()
