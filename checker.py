import os
import zlib
import struct
import re
import subprocess
import json
import sys
import requests

# --- КОНФИГУРАЦИЯ ---
API_URL = "https://script.google.com/macros/s/AKfycbzUhzbm-xpxCoQtJn0ndUsLQfpGEXBmLP2dCs8ky9MkQ2M3_5EKkWHnz99LKc3Fppc5/exec"

COLOR_TO_SLOT = {
    "#ff0303": 0, "#0042ff": 1, "#1ce6b9": 2, "#540081": 3, "#fffc00": 4,
    "#fe8a0e": 5, "#20c000": 6, "#e55bb0": 7, "#959697": 8, "#7ebff1": 9
}

def get_names_from_js(filename):
    if not os.path.exists("parser.js"): return {}
    try:
        res = subprocess.run(["node", "parser.js", filename], capture_output=True, text=True, encoding='utf-8', errors='replace')
        match = re.search(r'(\{.*\})', res.stdout.strip())
        if match:
            data = json.loads(match.group(1))
            return {int(k): v for k, v in data.items()}
    except: pass
    return {}

def decode_hero_int(val_int):
    try:
        if val_int == 0: return "-"
        chars = [chr((val_int >> 24) & 0xFF), chr((val_int >> 16) & 0xFF), chr((val_int >> 8) & 0xFF), chr(val_int & 0xFF)]
        res = "".join(chars).strip().replace('\x00', '')
        if len(res) == 4 and all(c.isalnum() for c in res): return res
        rev = res[::-1]
        if len(rev) == 4 and all(c.isalnum() for c in rev): return rev
        return "-"
    except: return "-"

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

def analyze_and_upload(filename):
    print(f"🚀 Обработка файла: {filename}")
    js_players = get_names_from_js(filename)
    raw = decompress(filename)
    if not raw: return

    text = raw.decode('latin-1', errors='ignore')
    players_by_slot = {}

    # 1. Заполняем базу игроков
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
            "hero": "Unknown", 
            "rounds": 0
        }

    # 2. Парсинг героев
    hero_int_matches = re.findall(r'VarP\W+(\d+)\W+Picked_hero\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in hero_int_matches:
        try:
            slot = int(pid_str)
            decoded = decode_hero_int(int(val_str))
            if slot in players_by_slot and decoded != "-":
                players_by_slot[slot]["hero"] = decoded
        except: pass

    # 3. Парсинг счета (Won_rounds)
    game_goal = 0
    goal_matches = re.findall(r'VarP\W+\d+\W+Rounds_to_win\D+?(\d+)', text, re.IGNORECASE)
    if goal_matches: game_goal = int(goal_matches[0])

    score_matches = re.findall(r'VarP\W+(\d+)\W+Won_rounds\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in score_matches:
        try:
            slot = int(pid_str)
            val = int(val_str)
            if slot in players_by_slot:
                if val > players_by_slot[slot]["rounds"]:
                    players_by_slot[slot]["rounds"] = val
        except: pass

    # --- ИЗМЕНЕНИЕ 1: Жесткое имя карты ---
    map_name = "Anime Choice Battle"

    # --- ИЗМЕНЕНИЕ 2: Логика Ban Mode ---
    game_mode = "Normal"
    gm_match = re.search(r'VarP\W+\d+\W+Game_Mode\W+\w+\W+([^\s\x00]+)', text, re.IGNORECASE)
    if gm_match: 
        raw_mode = gm_match.group(1)
        if "ban" in raw_mode.lower():
            game_mode = "Ban Mode"
        else:
            game_mode = raw_mode

    # --- ОПРЕДЕЛЕНИЕ ПОБЕДИТЕЛЯ ---
    t1 = [p for p in players_by_slot.values() if p['team'] == 1]
    t2 = [p for p in players_by_slot.values() if p['team'] == 2]

    s1_score = max([p['rounds'] for p in t1], default=0)
    s2_score = max([p['rounds'] for p in t2], default=0)

    # Синхронизация счета
    for p in t1: p['rounds'] = s1_score
    for p in t2: p['rounds'] = s2_score

    winner_team = 0
    if s1_score > s2_score: winner_team = 1
    elif s2_score > s1_score: winner_team = 2
    elif game_goal > 0 and (s1_score >= game_goal or s2_score >= game_goal):
         if s1_score >= s2_score: winner_team = 1
         else: winner_team = 2

    if winner_team == 0:
        print("⚠️ Ничья или игра не состоялась, пропускаем.")
        os.remove(filename)
        return

    # --- ОТПРАВКА ДАННЫХ ---
    payload = {
        "action": "add_game", # Важно для Google Script
        "file_name": os.path.basename(filename),
        "map_name": map_name,
        "mode": game_mode,     # Отправляем новый режим
        "winner_team": winner_team,
        "score_t1": s1_score,  # Отправляем счет T1
        "score_t2": s2_score,  # Отправляем счет T2
        "players": list(players_by_slot.values())
    }

    try:
        print(f"📤 Отправка: {map_name} ({game_mode}). Счет {s1_score}:{s2_score}")
        r = requests.post(API_URL, json=payload)
        
        if r.status_code == 200 and "Success" in r.text:
            print("✅ Данные сохранены!")
            os.remove(filename)
        elif "Skipped" in r.text:
            print("⚠️ Игра уже была записана.")
            os.remove(filename)
        else:
            print(f"❌ Ошибка сервера: {r.text}")
            
    except Exception as e:
        print(f"❌ Ошибка сети: {e}")

if __name__ == "__main__":
    replay_dir = "replays"
    if not os.path.exists(replay_dir): os.makedirs(replay_dir)
    
    files = [f for f in os.listdir(replay_dir) if f.endswith(".w3g")]
    
    if not files:
        print("📭 Нет новых реплеев.")
    else:
        for f in files:
            analyze_and_upload(os.path.join(replay_dir, f))
