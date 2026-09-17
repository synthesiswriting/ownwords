const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

test('distribution builder refuses unsafe archives and preserves source roots',()=>{
  const result=spawnSync('python3',['-B',path.resolve(__dirname,'../scripts/test-packaging.py')],{encoding:'utf8'});
  assert.equal(result.status,0,result.stdout+result.stderr);
});


test('curl installer verifies archives, ownership and interrupted recovery', () => {
  const result = spawnSync('python3', ['-B', 'scripts/test-curl-installer.py'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});


test('setup and curl cancellation reach their exact children', () => {
  const result = spawnSync('python3', ['-B', 'scripts/test-cancellation.py'], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
