const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

const CLI = path.resolve(__dirname, '../bin/ownwords.js');

test('distribution builder refuses unsafe archives and preserves source roots',()=>{
  const result=spawnSync('python3',['-B',path.resolve(__dirname,'../scripts/test-packaging.py')],{encoding:'utf8'});
  assert.equal(result.status,0,result.stdout+result.stderr);
});


test('curl installer verifies archives, ownership and interrupted recovery', () => {
  const result = spawnSync('python3', ['-B', path.resolve(__dirname, '../scripts/test-curl-installer.py')], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});


test('curl installer cancellation reaches its exact children', () => {
  const result = spawnSync('python3', ['-B', path.resolve(__dirname, '../scripts/test-cancellation.py')], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});


test('CLI help lists authoring commands and no setup or synthesis command', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'ownwords-help-'));
  try {
    const result = spawnSync(process.execPath, [CLI, '--help'], { env: { ...process.env, HOME: home }, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /^ {2}convert <input> \[output\] /m);
    assert.doesNotMatch(result.stdout, /^ {2}(setup|synthesis)\b/m);
    assert.deepEqual(fs.readdirSync(home), []);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});


test('removed setup and synthesis commands are unknown and leave home unchanged', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'ownwords-removed-'));
  try {
    for (const args of [['setup'], ['setup', '--no-dormant-core', '--json'], ['synthesis', 'status'], ['synthesis', 'activate', '--profile', 'full']]) {
      const result = spawnSync(process.execPath, [CLI, ...args], { env: { ...process.env, HOME: home }, encoding: 'utf8' });
      assert.equal(result.status, 1, `${args.join(' ')}: ${result.stdout}${result.stderr}`);
      assert.match(result.stderr, new RegExp(`^Unknown command: ${args[0]}$`, 'm'));
    }
    assert.deepEqual(fs.readdirSync(home), []);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});
