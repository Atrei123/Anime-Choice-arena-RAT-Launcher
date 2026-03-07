const fs = require('fs');
const axios = require('axios');
const path = require('path');
const cheerio = require('cheerio'); // 👈 НОВАЯ БИБЛИОТЕКА
const w3gjs_lib = require('w3gjs');
const W3GReplay = w3gjs_lib.default || w3gjs_lib;

// --- НАСТРОЙКИ ---
const BASE_URL = "https://replays.irinabot.ru/19374/";
const GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzUhzbm-xpxCoQtJn0ndUsLQfpGEXBmLP2dCs8ky9MkQ2M3_5EKkWHnz99LKc3Fppc5/exec";

// 📅 С какой даты начинать считать рейтинг?
const START_DATE = new Date('2025-12-01T00:00:00'); 

const HISTORY_FILE = path.resolve(__dirname, 'history.json');
const DEFAULT_COLOR = { name: "Unk", code: "\x1b[37m" };

// --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

function loadHistory() {
    if (fs.existsSync(HISTORY_FILE)) return JSON.parse(fs.readFileSync(HISTORY_FILE, 'utf8'));
    return [];
}

function saveHistory(history) {
    fs.writeFileSync(HISTORY_FILE, JSON.stringify(history, null, 2), 'utf8');
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Парсер даты из имени файла: GHost++_2025-12-20_01-46_...
function getDateFromFilename(filename) {
    try {
        // Ищем: 4 цифры - 2 цифры - 2 цифры _ 2 цифры - 2 цифры
        const regex = /(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})/;
        const match = filename.match(regex);
        if (match) {
            // new Date(Year, Month-1, Day, Hour, Minute)
            return new Date(match[1], match[2] - 1, match[3], match[4], match[5]);
        }
    } catch (e) { return null; }
    return null;
}

// Обработка одной игры
async function processOneGame(url, fileName) {
    console.log(`\n🔄 Скачиваю: ${fileName}`);
    const os = require('os');
    const filePath = path.resolve(__dirname, 'temp.w3g');
    
    try {
        const writer = fs.createWriteStream(filePath);
        const dl = await axios({ url: url, method: 'GET', responseType: 'stream' });
        dl.data.pipe(writer);
        await new Promise((r) => writer.on('finish', r));

        const fileBuffer = fs.readFileSync(filePath);
        const parser = new W3GReplay();
        const result = await parser.parse(fileBuffer);

        const players = result.players || result.data.game.players;
        if (!players || players.length === 0) {
            console.log("⚠️ Ошибка: пустой список игроков.");
            return false;
        }

        // АНАЛИЗ ПОБЕДИТЕЛЯ (APM Logic)
        const stats = [];
        players.forEach(p => {
            const teamId = p.teamid !== undefined ? p.teamid : p.team;
            if (teamId >= 12) return; 

            const apmArray = p.actions.timed || [];
            let lastActiveMinute = -1;
            let actionsInLastMinute = 0;
            for (let i = apmArray.length - 1; i >= 0; i--) {
                if (apmArray[i] > 0) {
                    lastActiveMinute = i;
                    actionsInLastMinute = apmArray[i];
                    break;
                }
            }
            stats.push({ name: p.name, team: teamId, lastMin: lastActiveMinute, lastActions: actionsInLastMinute });
        });

        stats.sort((a, b) => {
            if (b.lastMin !== a.lastMin) return b.lastMin - a.lastMin;
            return b.lastActions - a.lastActions;
        });

        const top3 = stats.slice(0, 3);
        const team1Score = top3.filter(s => s.team === 0).length;
        const winnerTeamId = team1Score >= 2 ? 0 : 1; 

        let mapName = "Unknown Map";
        if (result.header && result.header.mapName) mapName = result.header.mapName;

        console.log(`✅ Победила Team ${winnerTeamId + 1}. Отправляю...`);

        const payload = {
            winner_team: winnerTeamId + 1,
            map_name: mapName,
            file_name: fileName,
            players: stats.map(p => ({ name: p.name, team: p.team + 1 }))
        };

        await axios.post(GOOGLE_SCRIPT_URL, payload, { headers: { 'Content-Type': 'application/json' }, maxRedirects: 5 });
        console.log("🆗 Записано.");
        return true;

    } catch (e) {
        console.error("❌ Ошибка:", e.message);
        return false;
    }
}

async function main() {
    console.clear();
    console.log(`🚀 ЗАПУСК: Ищем игры...`);
    
    // Загружаем историю, чтобы не повторяться
    const history = loadHistory();
    console.log(`📂 В базе уже есть: ${history.length} игр`);

    try {
        const response = await axios.get(BASE_URL);
        const html = response.data;
        
        // --- ИСПОЛЬЗУЕМ CHEERIO (Надежный поиск) ---
        const $ = cheerio.load(html);
        const allGames = [];

        // Ищем все теги <a> у которых href заканчивается на .w3g
        $('a').each((index, element) => {
            const href = $(element).attr('href');
            
            if (href && href.endsWith('.w3g')) {
                // Если ссылка относительная (без http), добавляем домен
                let fullUrl = href;
                if (!href.startsWith('http')) {
                    // Убираем слеш в начале если есть, чтобы не было double slash
                    const cleanHref = href.startsWith('/') ? href.substring(1) : href;
                    fullUrl = BASE_URL + cleanHref;
                    
                    // Хак для ирины: если BASE_URL уже заканчивается на /, а href нет - просто склеиваем
                    // Если href это просто имя файла "game.w3g", то BASE_URL + href идеально работает
                }

                // Имя файла нужно для ID и даты. Обычно это часть после последнего слэша
                // Декодируем URL (чтобы убрать %20, %2B и т.д.)
                const fileName = decodeURIComponent(href.split('/').pop());
                
                // Пробуем достать дату
                const dateObj = getDateFromFilename(fileName);

                if (dateObj) {
                    allGames.push({ url: fullUrl, fileName: fileName, date: dateObj });
                }
            }
        });

        console.log(`🔎 Всего найдено .w3g файлов с датой: ${allGames.length}`);

        // 1. ФИЛЬТР: Только новые + Только свежие по дате
        const newGames = allGames.filter(g => {
            const isNewEnough = g.date >= START_DATE;
            const notInHistory = !history.includes(g.fileName);
            return isNewEnough && notInHistory;
        });

        console.log(`🆕 Из них нужно обработать: ${newGames.length}`);

        if (newGames.length === 0) {
            console.log("⚡️ Все игры уже обработаны.");
            
            // ДЕБАГ: Если 0, давай посмотрим, что он вообще нашел (первые 3)
            if (allGames.length > 0) {
                console.log("Пример того, что нашел, но отфильтровал (может дата старая?):");
                console.log(allGames.slice(0, 3));
            } else {
                console.log("⚠️ ВООБЩЕ НИЧЕГО НЕ НАШЕЛ. Возможно, сайт пустой или заблокировал нас.");
                console.log("HTML (первые 500 символов):");
                console.log(html.substring(0, 500));
            }
            return;
        }

        // 2. СОРТИРОВКА (Старые -> Новые)
        newGames.sort((a, b) => a.date - b.date);

        // 3. ОБРАБОТКА
        for (const game of newGames) {
            console.log(`-------------------------------------------`);
            const success = await processOneGame(game.url, game.fileName);
            if (success) {
                history.push(game.fileName);
                saveHistory(history);
                await sleep(1500); // Пауза
            }
        }

        console.log("\n🏁 ГОТОВО!");

    } catch (err) {
        console.error("Critical Error:", err);
    }
}


main();

