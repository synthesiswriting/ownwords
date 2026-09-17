'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

function verifyBundle(bundleRoot) {
  const root = path.resolve(bundleRoot);
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error('Setup bundle must be a regular directory');
  const manifestPath = path.join(root, 'bundle-integrity.json');
  if (!fs.lstatSync(manifestPath).isFile() || fs.lstatSync(manifestPath).isSymbolicLink()) throw new Error('Setup integrity manifest is invalid');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  if (manifest.schema_version !== 1 || !/^\d+\.\d+\.\d+$/.test(manifest.version) || !/^[a-f0-9]{40}$/.test(manifest.commit) || !/^[a-f0-9]{64}$/.test(manifest.archive_sha256) || !manifest.files || typeof manifest.files !== 'object' || Array.isArray(manifest.files)) {
    throw new Error('Setup integrity manifest is invalid');
  }
  const observed = [];
  function visit(directory) {
    for (const name of fs.readdirSync(directory)) {
      const file = path.join(directory, name);
      const relative = path.relative(root, file).split(path.sep).join('/');
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink() || (!stat.isFile() && !stat.isDirectory())) throw new Error('Setup bundle contains a link or special object');
      if (stat.isDirectory()) visit(file);
      else if (relative !== 'bundle-integrity.json') {
        observed.push(relative);
        const evidence = manifest.files[relative];
        if (!evidence || !/^[a-f0-9]{64}$/.test(evidence.sha256) || ![0o644, 0o755].includes(evidence.mode) || (stat.mode & 0o777) !== evidence.mode || crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== evidence.sha256) {
          throw new Error(`Setup bundle integrity changed: ${relative}`);
        }
      }
    }
  }
  visit(root);
  if (JSON.stringify(observed.sort()) !== JSON.stringify(Object.keys(manifest.files).sort()) || !manifest.files['bin/synthesis'] || !manifest.files['lib/release.json']) throw new Error('Setup bundle integrity membership changed');
  const release = JSON.parse(fs.readFileSync(path.join(root, 'lib/release.json'), 'utf8'));
  if (release.version !== manifest.version || release.commit !== manifest.commit) throw new Error('Setup release identity differs from its integrity manifest');
  return manifest;
}

function runSetup(args, options = {}) {
  const allowed = new Set(['--no-dormant-core', '--json', '--help', '-h']);
  if (args.some(argument => !allowed.has(argument)) || new Set(args).size !== args.length) {
    throw new Error('Usage: ownwords setup [--no-dormant-core] [--json]');
  }
  if (args.includes('--help') || args.includes('-h')) {
    console.log('ownwords setup [--no-dormant-core] [--json]\nExplicitly stage dormant Synthesis assets without configuring agents, hooks, services, articles or accounts.\n--no-dormant-core declines staging and preserves existing installations.');
    return 0;
  }
  return runVerified(['stage-core', '--for-tool', 'ownwords', ...args], options);
}

function runSynthesis(args, options = {}) {
  const usage = 'Usage: ownwords synthesis <activate|deactivate|status|doctor|repair|update> [args]';
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) {
    console.log(`${usage}\nExplicitly manage Synthesis through the bundled launcher.\nExample: ownwords synthesis activate --profile full\nUse --profile skills-only to activate the skill catalog without the full workspace.`);
    return 0;
  }
  const allowed = new Set(['activate', 'deactivate', 'status', 'doctor', 'repair', 'update']);
  if (!allowed.has(args[0])) throw new Error(usage);
  return runVerified(args, options);
}

function runVerified(args, options) {
  const bundle = options.bundleRoot || path.join(__dirname, '..', 'vendor', 'synthesis');
  try {
    verifyBundle(bundle);
  } catch (error) {
    throw new Error(`Setup bundle verification failed: ${error.message}`);
  }
  return new Promise((resolve, reject) => {
    const grouped = process.platform !== 'win32';
    const child = spawn(path.join(bundle, 'bin', 'synthesis'), args, {
      stdio: 'inherit', env: options.env || process.env, detached: grouped
    });
    let cancelled = null;
    const handlers = new Map();
    const cleanup = () => {
      for (const [signal, handler] of handlers) process.removeListener(signal, handler);
    };
    for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
      const handler = () => {
        cancelled ||= signal;
        try {
          // The new group belongs only to this invocation, including its descendants.
          if (grouped && child.pid) process.kill(-child.pid, signal);
          else child.kill(signal);
        } catch (error) {
          if (error.code !== 'ESRCH') reject(error);
        }
      };
      handlers.set(signal, handler);
      process.on(signal, handler);
    }
    child.once('error', error => {
      cleanup();
      reject(new Error(`Setup could not start: ${error.message}`));
    });
    child.once('close', (code, signal) => {
      cleanup();
      const termination = cancelled || signal;
      if (termination) process.kill(process.pid, termination);
      resolve(Number.isInteger(code) ? code : 1);
    });
  });
}

module.exports = { runSetup, runSynthesis, verifyBundle };
