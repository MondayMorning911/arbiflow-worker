const fs = require('fs');
const lines = fs.readFileSync('bot.py', 'utf8').split('\n');
const newLines = lines.filter((_, i) => i < 88 || i >= 475);
fs.writeFileSync('bot.py', newLines.join('\n'));
