const { execSync } = require('child_process');
try {
  execSync('python3 -m py_compile bot.py', { stdio: 'inherit' });
  console.log('Syntax OK');
} catch (e) {
  console.error('Syntax Error');
}
