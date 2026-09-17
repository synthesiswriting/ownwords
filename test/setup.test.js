const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { runSetup, runSynthesis, verifyBundle } = require('../lib/setup');

function fixture(launcherOverride) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ownwords-setup-'));
  const bundle = path.join(root, 'bundle');
  fs.mkdirSync(path.join(bundle, 'bin'), { recursive: true });
  fs.mkdirSync(path.join(bundle, 'lib'));
  const launcher = launcherOverride || '#!/bin/sh\nset -eu\nprintf "%s\\n" "$@" > "$OWNWORDS_FIXTURE_ARGUMENTS"\n';
  fs.writeFileSync(path.join(bundle, 'bin/synthesis'), launcher, { mode: 0o755 });
  fs.writeFileSync(path.join(bundle, 'lib/release.json'), JSON.stringify({schema_version:1,version:'4.100.3',commit:'a'.repeat(40)}));
  const files = {};
  for (const name of ['bin/synthesis','lib/release.json']) {
    const file = path.join(bundle, name);
    files[name] = {sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),mode:fs.statSync(file).mode & 0o777};
  }
  fs.writeFileSync(path.join(bundle, 'bundle-integrity.json'), JSON.stringify({schema_version:1,version:'4.100.3',commit:'a'.repeat(40),archive_sha256:'b'.repeat(64),files}));
  return {root,bundle};
}

test('explicit setup delegates only inert tool staging', async () => {
  const {root,bundle}=fixture();
  try {
    const output=path.join(root,'arguments');
    assert.equal(await runSetup(['--no-dormant-core'],{bundleRoot:bundle,env:{...process.env,OWNWORDS_FIXTURE_ARGUMENTS:output}}),0);
    assert.deepEqual(fs.readFileSync(output,'utf8').trim().split('\n'),['stage-core','--for-tool','ownwords','--no-dormant-core']);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('corrupted or extra vendor bytes are refused before execution', () => {
  const {root,bundle}=fixture();
  try {
    assert.equal(verifyBundle(bundle).version,'4.100.3');
    fs.appendFileSync(path.join(bundle,'bin/synthesis'),'# tamper');
    assert.throws(()=>verifyBundle(bundle),/integrity|changed/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('setup rejects article and installer arguments', () => {
  assert.throws(()=>runSetup(['--api']),/setup/);
  assert.throws(()=>runSetup(['https://example.invalid/article']),/setup/);
  assert.throws(()=>runSetup(['--profile','full']),/setup/);
});

test('ordinary CLI help does not stage or change a clean home', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ownwords-ordinary-'));
  try {
    const result=spawnSync(process.execPath,[path.resolve('bin/ownwords.js'),'--help'],{env:{...process.env,HOME:root},encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    assert.deepEqual(fs.readdirSync(root),[]);
    assert.match(result.stdout,/setup/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('default setup forwards an explicit ownwords staging request', async () => {
  const {root,bundle}=fixture();
  try {
    const output=path.join(root,'arguments');
    assert.equal(await runSetup([],{bundleRoot:bundle,env:{...process.env,OWNWORDS_FIXTURE_ARGUMENTS:output}}),0);
    assert.deepEqual(fs.readFileSync(output,'utf8').trim().split('\n'),['stage-core','--for-tool','ownwords']);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('unknown vendor file and symlink targets fail closed', () => {
  const {root,bundle}=fixture();
  try {
    const unexpected=path.join(bundle,'extra.py');
    fs.writeFileSync(unexpected,'# unexpected code');
    assert.throws(()=>verifyBundle(bundle),/integrity/);
    fs.unlinkSync(unexpected);
    fs.symlinkSync(path.join(root,'foreign'),unexpected);
    assert.throws(()=>verifyBundle(bundle),/link/);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});


test('explicit synthesis bridge forwards allowed commands and arguments exactly', async () => {
  const {root,bundle}=fixture();
  try {
    const output=path.join(root,'arguments');
    for (const command of ['activate','deactivate','status','doctor','repair','update']) {
      const args=[command,'--profile','full','path with spaces','--literal=;value',''];
      assert.equal(await runSynthesis(args,{bundleRoot:bundle,env:{...process.env,OWNWORDS_FIXTURE_ARGUMENTS:output}}),0);
      assert.deepEqual(fs.readFileSync(output,'utf8').split('\n').slice(0,-1),args);
    }
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('synthesis bridge refuses commands outside its lifecycle allowlist', () => {
  for (const args of [[],['setup'],['stage-core'],['enroll'],['publish'],['--profile','full']]) {
    assert.throws(()=>runSynthesis(args),/Usage: ownwords synthesis/);
  }
});

test('synthesis bridge propagates child failures', async () => {
  const {root,bundle}=fixture('#!/bin/sh\nexit 23\n');
  try {
    assert.equal(await runSynthesis(['doctor'],{bundleRoot:bundle}),23);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('setup and synthesis bridge propagate child termination signals', () => {
  const {root,bundle}=fixture('#!/bin/sh\nkill -TERM $$\n');
  try {
    for (const method of ['runSetup','runSynthesis']) {
      const args=method==='runSetup'?[]:['doctor'];
      const script=`const m=require(${JSON.stringify(path.resolve('lib/setup.js'))}); Promise.resolve(m[${JSON.stringify(method)}](${JSON.stringify(args)},{bundleRoot:${JSON.stringify(bundle)}})).then(code=>process.exit(code));`;
      const result=spawnSync(process.execPath,['-e',script],{encoding:'utf8'});
      assert.equal(result.signal,'SIGTERM',result.stderr);
      assert.equal(result.status,null);
    }
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('bridge help is available without a bundle or article modules', () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'ownwords-bridge-help-'));
  try {
    const result=spawnSync(process.execPath,[path.resolve('bin/ownwords.js'),'synthesis','--help'],{env:{...process.env,HOME:root},encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
    assert.match(result.stdout,/ownwords synthesis activate --profile full/);
    assert.deepEqual(fs.readdirSync(root),[]);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
