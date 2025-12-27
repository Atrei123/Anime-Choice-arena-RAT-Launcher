import os
import zlib
import struct
import re
import csv
import subprocess
import json
import sys

# ТАБЛИЦА ЦВЕТОВ
COLOR_TO_SLOT = {
    "#ff0303": 0, "#0042ff": 1, "#1ce6b9": 2, "#540081": 3, "#fffc00": 4,
    "#fe8a0e": 5, "#20c000": 6, "#e55bb0": 7, "#959697": 8, "#7ebff1": 9
}

# --- 1. ВЫЗОВ JS ---
def get_names_from_js(filename):
    if not os.path.exists("parser.js"): return {}
    try:
        res = subprocess.run(["node", "parser.js", filename], capture_output=True, text=True, encoding='utf-8', errors='replace')
        raw = res.stdout.strip()
        match = re.search(r'(\{.*\})', raw)
        if match:
            data = json.loads(match.group(1))
            return {int(k): v for k, v in data.items()}
    except: pass
    return {}

# --- 2. ДЕКОДЕР ЧИСЛА В ID ГЕРОЯ ---
def decode_hero_int(val_int):
    try:
        if val_int == 0: return "-"
        chars = [chr((val_int >> 24) & 0xFF), chr((val_int >> 16) & 0xFF), chr((val_int >> 8) & 0xFF), chr(val_int & 0xFF)]
        res = "".join(chars).strip().replace('\x00', '')
        if len(res) == 4 and all(c.isalnum() for c in res):
            return res
        rev = res[::-1]
        if len(rev) == 4 and all(c.isalnum() for c in rev):
            return rev
        return "-"
    except: return "-"

# --- 3. РАСПАКОВКА ---
def decompress(filename):
    if not os.path.exists(filename): return None
    decompressed = bytearray()
    with open(filename, 'rb') as f: data = f.read()
    i = 0
    size = len(data)
    if size > 1000: decompressed.extend(data[:1000])
    while i < size - 8:
        try:
            c, d, _ = struct.unpack('<HHI', data[i:i+8])
            if c > 0 and (i + 8 + c <= size) and d <= 65536:
                chunk = data[i+8 : i+8+c]
                try:
                    dec = zlib.decompress(chunk)
                    if len(dec) == d:
                        decompressed.extend(dec)
                        i += 8 + c
                        continue
                except: pass
            i += 1
        except: i += 1
    return decompressed

# --- 4. АНАЛИЗАТОР ---
def analyze(filename):
    print(f"🚀 Анализ: {filename}")
    
    js_players = get_names_from_js(filename)
    raw = decompress(filename)
    if not raw: 
        print("❌ Ошибка чтения файла")
        return

    text = raw.decode('latin-1', errors='ignore')
    
    players_by_slot = {}
    
    # 1. Заполняем базу по Слотам
    for pid, info in js_players.items():
        name = info.get('name')
        if not name: continue
        
        slot = info.get('slot', -1)
        if slot == -1 and 'color' in info:
            color = info['color'].lower()
            if color in COLOR_TO_SLOT: slot = COLOR_TO_SLOT[color]

        if slot >= 24 or slot < 0: continue 

        team_num = 1 if slot < 5 else 2

        players_by_slot[slot] = {
            "name": name,
            "slot": slot,
            "team": team_num,
            "hero": "-",
            "rounds": 0,
            "status": "Played"
        }

    # 2. ПАРСИНГ ГЕРОЕВ (ЧИСЛОВОЙ + СТРОКОВЫЙ)
    # Числовой (приоритет)
    hero_int_matches = re.findall(r'VarP\W+(\d+)\W+Picked_hero\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in hero_int_matches:
        slot = int(pid_str)
        try:
            decoded = decode_hero_int(int(val_str))
            if slot in players_by_slot and decoded != "-":
                players_by_slot[slot]["hero"] = decoded
        except: pass

    # Строковый (резерв)
    hero_str_matches = re.findall(r'VarP\W+(\d+)\W+Picked_hero\W+\w+\W+([a-zA-Z0-9]{4})\b', text, re.IGNORECASE)
    for pid_str, val in hero_str_matches:
        slot = int(pid_str)
        if slot in players_by_slot and players_by_slot[slot]["hero"] == "-":
             if val.lower() != "none" and val.lower() != "null":
                players_by_slot[slot]["hero"] = val

    # 3. ПАРСИНГ ЦЕЛИ (Rounds_to_win)
    game_goal = 0
    goal_matches = re.findall(r'VarP\W+\d+\W+Rounds_to_win\D+?(\d+)', text, re.IGNORECASE)
    if goal_matches:
        try: game_goal = int(goal_matches[0])
        except: pass

    # 4. ПАРСИНГ СЧЕТА (Won_rounds)
    score_matches = re.findall(r'VarP\W+(\d+)\W+Won_rounds\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in score_matches:
        slot = int(pid_str)
        try:
            val = int(val_str)
            if slot in players_by_slot:
                if val > players_by_slot[slot]["rounds"]:
                    players_by_slot[slot]["rounds"] = val
        except: pass

    # 5. ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
    game_mode = "Normal"
    gm_match = re.search(r'VarP\W+\d+\W+Game_Mode\W+\w+\W+([^\s\x00]+)', text, re.IGNORECASE)
    if gm_match: game_mode = gm_match.group(1)

    # --- ИТОГИ И СИНХРОНИЗАЦИЯ КОМАНД ---
    t1 = [p for p in players_by_slot.values() if p['team'] == 1]
    t2 = [p for p in players_by_slot.values() if p['team'] == 2]

    # Находим лучший счет в команде
    s1_score = max([p['rounds'] for p in t1], default=0)
    s2_score = max([p['rounds'] for p in t2], default=0)

    # !!! СИНХРОНИЗАЦИЯ !!!
    # Присваиваем командный счет всем участникам (чиним нули у Motorka и 2thedevil2)
    for p in t1: p['rounds'] = s1_score
    for p in t2: p['rounds'] = s2_score

    winner = "Draw"
    if s1_score > s2_score: winner = "Team 1"
    elif s2_score > s1_score: winner = "Team 2"
    elif game_goal > 0 and (s1_score >= game_goal or s2_score >= game_goal):
         if s1_score >= s2_score: winner = "Team 1"
         else: winner = "Team 2"

    map_name = "Unknown"
    map_match = re.search(r'mapname\W+([^\x00\r\n]+)', text, re.IGNORECASE)
    if map_match: 
        map_name = re.sub(r'\.w3[xm]$', '', map_match.group(1), flags=re.IGNORECASE).strip()

    # --- ВЫВОД ---
    print("\n" + "="*70)
    print(f"🏆 {winner} (Счет {s1_score} : {s2_score})")
    print(f"🗺️  {map_name} | Mode: {game_mode} | Goal: {game_goal}")
    print("="*70)
    print(f"{'Slot':<4} | {'Team':<6} | {'Nick':<20} | {'Hero':<6} | {'Rnd':<3} | {'Status'}")
    print("-" * 70)
    
    t1.sort(key=lambda x: x['slot'])
    t2.sort(key=lambda x: x['slot'])
    
    def get_status(team, winner_team):
        if winner_team == "Draw": return "Draw"
        if (team == 1 and winner_team == "Team 1") or (team == 2 and winner_team == "Team 2"):
            return "Winner"
        return "Loser"

    for p in t1:
        print(f"{p['slot']:<4} | T1     | {p['name']:<20} | {p['hero']:<6} | {p['rounds']:<3} | {get_status(1, winner)}")
    print("-" * 70)
    for p in t2:
        print(f"{p['slot']:<4} | T2     | {p['name']:<20} | {p['hero']:<6} | {p['rounds']:<3} | {get_status(2, winner)}")
    print("="*70)

    # CSV
    try:
        with open("last_game_stats.csv", 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(["Map", "Mode", "Winner", "Player", "Slot", "Team", "Hero", "Rounds", "Status"])
            for p in t1 + t2:
                writer.writerow([
                    map_name, game_mode, winner,
                    p["name"], p["slot"], f"Team {p['team']}", p["hero"], p["rounds"], 
                    get_status(p['team'], winner)
                ])
        print("💾 CSV файл сохранен.")
    except Exception as e:
        print(f"⚠️ Ошибка CSV: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1: f = sys.argv[1]
    else: f = input("Файл .w3g: ").strip().replace('"','')
    analyze(f)