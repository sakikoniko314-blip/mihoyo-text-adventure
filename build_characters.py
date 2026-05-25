"""Extract world info: characters, locations, factions from game data."""
import json, os, re

DATA = r"C:\Users\misak\Desktop\zlb-scraper\data"
OUTPUT = r"C:\Users\misak\Desktop\zlb-scraper\world_info.json"

GI_NOISE = re.compile(
    r"|".join([
        r"^\d", r"一[^个]", r"(告示|海报|广告|石碑|书信|日记|记录|乐稿|乐谱|残页|公告|卷|书|信|笔记|文件|铭文|碑文|石板)",
        r"(任务道具|教程)", r"[的之]\s*$", r"^(关于|有关|一位|一个)",
        r"丘丘", r"史莱姆", r"骗骗花", r"深渊", r"愚人众", r"盗宝团",
        r"千岩军", r"同心|与力", r"冒险家", r"商人$", r"小贩$", r"店主$",
        r"采药人", r"渔夫", r"铁匠", r"厨师", r"酒保", r"学者$",
        r"NPC", r"npc", r"怪物", r"敌人", r"精英", r"头目", r"首领",
        r"商店", r"客栈", r"杂货", r"武器店", r"锻造", r"委托",
        r"的.*第[一二三四五六七八九十]", r"的.*卷",
        r"-\d+$", r"图鉴", r"教程",
    ])
)

HSR_NOISE = re.compile(
    r"|".join([
        r"^\d", r"虚空", r"模拟", r"敌人", r"怪物", r"首领",
        r"NPC", r"npc", r"杂兵", r"喽啰",
    ])
)

EXTRA_EXCLUDE = {"archer", "saber", "unknown", "???", "开拓者", "旅行者"}

GI_REGIONS = {"蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "至冬", "坎瑞亚", "天空岛", "渊下宫", "层岩巨渊"}
HSR_FACTIONS = {"星穹列车", "仙舟联盟", "罗浮", "星际和平公司", "星核猎手", "焚化工", "天才俱乐部", "流光忆庭", "假面愚人", "泯灭帮", "家族", "猎犬家系", "同谐", "巡猎", "毁灭", "存护", "智识", "丰饶", "虚无", "记忆", "开拓", "地火", "银鬃铁卫", "机械聚落"}
HSR_LOCATIONS = {"黑塔空间站", "雅利洛-VI", "贝洛伯格", "磐岩镇", "仙舟罗浮", "匹诺康尼", "翁法罗斯", "奥赫玛"}

GI_KNOWN_FACTIONS = {
    "蒙德": ["西风骑士团", "西风教会", "冒险家协会蒙德分会"],
    "璃月": ["璃月七星", "往生堂", "南十字船队", "不卜庐"],
    "稻妻": ["天领奉行", "社奉行", "勘定奉行", "珊瑚宫", "海祇岛"],
    "须弥": ["教令院", "镀金旅团", "大风纪官"],
    "枫丹": ["枫丹廷", "沫芒宫", "逐影猎人"],
    "纳塔": ["古名", "回声之子"],
    "至冬": ["愚人众", "十一位执行官"],
}


def clean_name(name):
    name = name.strip()
    name = re.sub(r"[-–—].*$", "", name)
    name = re.sub(r"^[的之]", "", name)
    return name.strip()


def should_exclude(name, domain):
    if len(name) < 1 or len(name) > 20:
        return True
    if name.lower() in EXTRA_EXCLUDE:
        return True
    noise = GI_NOISE if domain == "gi" else HSR_NOISE
    if noise.search(name):
        return True
    return False


def extract(domain):
    index_path = os.path.join(DATA, domain, "index.json")
    with open(index_path, encoding="utf-8") as f:
        entries = json.load(f)

    characters = set()
    locations = set()
    categories_seen = set()

    for e in entries:
        t = e.get("type", "")
        c = e.get("category", "")
        path = e.get("relativePath", "")
        name = clean_name(e.get("name", ""))

        if not should_exclude(name, domain):
            if domain == "gi":
                is_char = t == "角色" or c == "角色资料" or ("角色" in path.split("/")[0])
            else:
                is_char = t == "Characters" or ("characters" in path.lower().split("/")[0])
            if is_char:
                characters.add(name)

        if c and c not in categories_seen:
            categories_seen.add(c)

        if domain == "gi":
            for r in GI_REGIONS:
                if r in path or r in name or r == c:
                    locations.add(r)

    if domain == "gi":
        locs = sorted(locations)
        factions = []
        for r in locs:
            if r in GI_KNOWN_FACTIONS:
                for f in GI_KNOWN_FACTIONS[r]:
                    factions.append((r, f))
    else:
        locs = sorted(HSR_LOCATIONS)
        factions = sorted(HSR_FACTIONS)

    # Normalize factions: if list of tuples, extract names; if plain list, use as-is
    if factions and isinstance(factions[0], tuple):
        faction_names = sorted(set(name for _, name in factions))
    else:
        faction_names = sorted(set(factions))

    return {
        "characters": sorted(characters),
        "locations": locs,
        "factions": faction_names,
    }


result = {}
for domain in ["gi", "hsr"]:
    data = extract(domain)
    result[domain] = data
    print(f"\n=== {domain.upper()} ===")
    print(f"  Characters: {len(data['characters'])}")
    print(f"  Locations:  {len(data['locations'])}: {', '.join(data['locations'][:10])}")
    print(f"  Factions:   {len(data['factions'])}: {', '.join(data['factions'][:10])}")

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"\nWritten to {OUTPUT}")
