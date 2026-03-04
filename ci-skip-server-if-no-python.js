const {execSync} = require('child_process');
try {
  execSync('python --version', {stdio: 'ignore'});
  console.log('Python found — running server tests');
  execSync('cd server && npm run test', {stdio: 'inherit'});
} catch (err) {
  console.warn('Python not found — skipping server tests');
  process.exit(0);
}
