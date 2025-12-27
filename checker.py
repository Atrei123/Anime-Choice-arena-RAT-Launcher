import os
import zlib
import struct
import re
import subprocess
import json
import sys
import requests
from urllib.parse import urljoin, unquote

# --- НАСТРОЙКИ ---
IRINA_URL = "https://replays.irinabot.ru/19374/"
API_URL = "https://script.google.com/macros/s/AKfycbzUhzbm-xpxCoQtJn0ndUsLQfpGEXBmLP2dCs8ky9MkQ2M3_5EKkWHnz99LKc3Fppc5/exec"
HISTORY_FILE = "history.json"
TEMP_DIR = "temp_replays"

COLOR_TO_SLOT = {
    "#ff0303": 0, "#0042ff": 1, "#1ce6b9": 2, "#540081": 3, "#fffc00": 4,
    "#fe8a0e": 5, "#20c000": 6, "#e55bb0": 7, "#959697": 8, "#7ebff1": 9
}

# --- РАБОТА С ИСТОРИЕЙ ---
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_history(processed_files):
    # Сортируем и сохраняем, чтобы файл был аккуратным
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(sorted(list(set(processed_files))), f, indent=2, ensure_ascii=False)

# --- ПАРСЕР W3G (JS) ---
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

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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

# --- АНАЛИЗАТОР ---
def analyze_game(filename, original_name):
    print(f"⚙️ Обработка: {original_name}")
    js_players = get_names_from_js(filename)
    raw = decompress(filename)
    if not raw: return None

    text = raw.decode('latin-1', errors='ignore')
    players_by_slot = {}

    # 1. Игроки
    for pid, info in js_players.items():
        name = info.get('name')
        if not name: continue
        slot = info.get('slot', -1)
        if slot == -1 and 'color' in info:
            color = info['color'].lower()
            if color in COLOR_TO_SLOT: slot = COLOR_TO_SLOT[color]
        if slot >= 24 or slot < 0: continue
        team_num = 1 if slot < 5 else 2
        players_by_slot[slot] = {"name": name, "slot": slot, "team": team_num, "hero": "Unknown", "rounds": 0}

    # 2. Герои
    hero_int_matches = re.findall(r'VarP\W+(\d+)\W+Picked_hero\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in hero_int_matches:
        try:
            slot = int(pid_str)
            decoded = decode_hero_int(int(val_str))
            if slot in players_by_slot and decoded != "-":
                players_by_slot[slot]["hero"] = decoded
        except: pass

    # 3. Счет
    game_goal = 0
    goal_matches = re.findall(r'VarP\W+\d+\W+Rounds_to_win\D+?(\d+)', text, re.IGNORECASE)
    if goal_matches: game_goal = int(goal_matches[0])

    score_matches = re.findall(r'VarP\W+(\d+)\W+Won_rounds\D+?(\d+)', text, re.IGNORECASE)
    for pid_str, val_str in score_matches:
        try:
            slot = int(pid_str)
            val = int(val_str)
            if slot in players_by_slot and val > players_by_slot[slot]["rounds"]:
                players_by_slot[slot]["rounds"] = val
        except: pass

    # 4. Карта и Режим (ЖЕСТКО ЗАДАЕМ ИМЯ КАРТЫ)
    map_name = "Anime Choice Battle"
    
    # Логика Ban Mode
    game_mode = "Normal"
    gm_match = re.search(r'VarP\W+\d+\W+Game_Mode\W+\w+\W+([^\s\x00]+)', text, re.IGNORECASE)
    if gm_match: 
        raw_mode = gm_match.group(1)
        if "ban" in raw_mode.lower(): game_mode = "Ban Mode"
        else: game_mode = raw_mode

    # 5. Победитель
    t1 = [p for p in players_by_slot.values() if p['team'] == 1]
    t2 = [p for p in players_by_slot.values() if p['team'] == 2]
    s1_score = max([p['rounds'] for p in t1], default=0)
    s2_score = max([p['rounds'] for p in t2], default=0)

    for p in t1: p['rounds'] = s1_score
    for p in t2: p['rounds'] = s2_score

    winner_team = 0
    if s1_score > s2_score: winner_team = 1
    elif s2_score > s1_score: winner_team = 2
    elif game_goal > 0 and (s1_score >= game_goal or s2_score >= game_goal):
         if s1_score >= s2_score: winner_team = 1
         else: winner_team = 2

    if winner_team == 0: return None # Ничья или ошибка

    return {
        "action": "add_game",
        "file_name": original_name,
        "map_name": map_name,
        "mode": game_mode,
        "winner_team": winner_team,
        "score_t1": s1_score,
        "score_t2": s2_score,
        "players": list(players_by_slot.values())
    }

# --- ГЛАВНАЯ ЛОГИКА ---
def run_cycle():
    # 1. Загружаем историю
    history = load_history()
    print(f"📂 В истории записей: {len(history)}")

    # 2. Получаем список файлов с сайта
    if not os.path.exists(TEMP_DIR): os.makedirs(TEMP_DIR)
    
    print(f"🌍 Сканируем {IRINA_URL}...")
    try:
        r = requests.get(IRINA_URL, timeout=10)
        # Ищем все ссылки, заканчивающиеся на .w3g
        # Ссылка может быть в href="filename.w3g"
        links = re.findall(r'href="([^"]+\.w3g)"', r.text)
        
        # Фильтруем дубликаты ссылок
        unique_links = list(set(links))
        
        print(f"🔍 Найдено файлов на сайте: {len(unique_links)}")
        
        new_files = []
        for link in unique_links:
            # Декодируем имя файла (убираем %20 и т.д.)
            filename = unquote(link)
            if filename not in history:
                new_files.append(link)
        
        print(f"🆕 Новых файлов для обработки: {len(new_files)}")

        # 3. Скачиваем и обрабатываем новые файлы
        for link in new_files:
            decoded_name = unquote(link)
            file_url = urljoin(IRINA_URL, link)
            local_path = os.path.join(TEMP_DIR, decoded_name)
            
            print(f"⬇️ Скачивание: {decoded_name}")
            try:
                # Скачиваем файл
                with requests.get(file_url, stream=True) as rf:
                    rf.raise_for_status()
                    with open(local_path, 'wb') as f:
                        for chunk in rf.iter_content(chunk_size=8192):
                            f.write(chunk)
                
                # Анализируем
                payload = analyze_game(local_path, decoded_name)
                
                if payload:
                    # Отправляем в Google Script
                    print(f"📤 Отправка данных на сервер...")
                    res = requests.post(API_URL, json=payload)
                    if res.status_code == 200:
                        print("✅ Успех!")
                        history.append(decoded_name) # Добавляем в историю только если сервер принял
                    else:
                        print(f"❌ Ошибка сервера: {res.text}")
                else:
                    print("⚠️ Невалидная игра (ничья или ошибка парсинга), помечаем как обработанную.")
                    # Добавляем в историю, чтобы не качать сломанный файл вечно
                    history.append(decoded_name)
                
                # Удаляем временный файл
                os.remove(local_path)
                
            except Exception as e:
                print(f"❌ Ошибка при обработке {decoded_name}: {e}")

    except Exception as e:
        print(f"❌ Ошибка доступа к сайту: {e}")

    # 4. Сохраняем историю
    save_history(history)
    print("💾 История обновлена.")

if __name__ == "__main__":
    run_cycle()
