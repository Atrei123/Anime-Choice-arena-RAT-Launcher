const fs = require('fs');

// Глушим консоль
console.log = function() {};
console.debug = function() {};
console.info = function() {};
console.warn = function() {};

function printJSON(data) {
    process.stdout.write(JSON.stringify(data));
}

let W3GReplay;
try {
    const lib = require('w3gjs');
    W3GReplay = lib.default || lib;
} catch (e) {
    printJSON({});
    process.exit(0);
}

const filePath = process.argv[2];
if (!filePath || !fs.existsSync(filePath)) {
    printJSON({});
    process.exit(0);
}

// РЕКУРСИВНЫЙ ПОИСК
const foundPlayers = {};

function scanForPlayers(obj) {
    if (!obj || typeof obj !== 'object') return;

    if (obj.name && typeof obj.name === 'string' && (typeof obj.id === 'number' || typeof obj.playerId === 'number')) {
        let pid = (typeof obj.id === 'number') ? obj.id : obj.playerId;
        let name = obj.name;

        // Хак с ресурсами
        if ((name === "Player" || name === "") && obj.resourceTransfers) {
            for (const t of obj.resourceTransfers) {
                if (t.playerName && t.playerName.length > 1) {
                    name = t.playerName;
                    break;
                }
            }
        }

        if (name && name.length > 1) {
            if (!foundPlayers[pid]) foundPlayers[pid] = {};
            foundPlayers[pid].name = name;
            
            // Ищем слот
            if (obj.slot !== undefined) foundPlayers[pid].slot = obj.slot;
            else if (obj.slotId !== undefined) foundPlayers[pid].slot = obj.slotId;
            else if (obj.teamid !== undefined) foundPlayers[pid].teamid = obj.teamid; // Как запасной вариант
            
            // Цвет для определения слота
            if (obj.color !== undefined) foundPlayers[pid].color = obj.color;
        }
    }

    for (const key in obj) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) {
            scanForPlayers(obj[key]);
        }
    }
}

async function parse() {
    try {
        const parser = new W3GReplay();
        const result = await parser.parse(filePath);
        scanForPlayers(result);
        printJSON(foundPlayers);
    } catch (e) {
        printJSON({});
    }
}

parse();