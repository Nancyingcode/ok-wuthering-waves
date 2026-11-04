const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');

const SOURCE_REPOSITORY = 'Nancyingcode/ok-wuthering-waves';
const DISTRIBUTION_ID = 'ok-ww-personal-runtime-v1';
const MANIFEST = 'runtime-manifest.json';
const BUDGET = 60 * 1024 * 1024;
const REQUIRED = ['main.py', 'main_debug.py', 'config.py', 'requirements.txt', 'pyappify.yml', 'README.md', 'LICENSE.txt'];
const RESOURCES = ['src', 'assets', 'icons', 'i18n', 'readme'];
const FORBIDDEN = new Set(['.git', '.github', '.agents', '.codex', '.venv', 'venv', 'node_modules',
  'build', 'dist', '__pycache__', 'configs', 'logs', 'cache', 'screenshots', 'click_screenshots',
  'ok_templates', 'tests', 'docs', 'custom_teams', 'working', 'updates', 'downloads']);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const json = value => `${JSON.stringify(value, null, 2)}\n`;
const git = (root, args, options = {}) => execFileSync('git', ['-C', root, ...args], {
  maxBuffer: 256 * 1024 * 1024, ...options,
});

function validateVersion(version) {
  // Match the pinned PyAppify parser, including its u64 range, without accepting
  // arbitrary semver suffixes that the client's version list would silently omit.
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:(?:-|\.)(alpha|beta|rc)(?:\.(\d+))?)?$/.exec(version);
  const numbers = match && [match[1], match[2], match[3], match[5]].filter(value => value !== undefined);
  if (!numbers || numbers.some(value => BigInt(value) > 18446744073709551615n)) {
    throw new Error('Version must be a PyAppify version: vMAJOR.MINOR.PATCH, optionally -alpha, -beta.1 or -rc.1.');
  }
  return version;
}
const isPrerelease = version => /(?:-|\.)(alpha|beta|rc)/.test(version);

function validatePath(name) {
  const parts = name.split('/');
  const invalid = !name || name !== name.normalize('NFC') || path.isAbsolute(name) ||
    parts.some(part => !part || part === '.' || part === '..' || /[\\:<>"|?*\x00-\x1f\x7f]/.test(part) ||
      /[. ]$/.test(part) || /^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(part));
  if (invalid) throw new Error(`Unsafe runtime path: ${name}`);
  if (parts.some(part => FORBIDDEN.has(part.toLowerCase()))) throw new Error(`Forbidden runtime path: ${name}`);
  return name;
}

function parsePolicy(bytes) {
  const entries = bytes.toString('utf8').split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('#'));
  const seen = new Set();
  for (const entry of entries) {
    validatePath(entry);
    if (entry === MANIFEST || seen.has(entry.toLowerCase())) throw new Error(`Duplicate/reserved policy entry: ${entry}`);
    seen.add(entry.toLowerCase());
  }
  if (!entries.length) throw new Error('Empty runtime policy');
  return entries;
}

function readTree(root, commit) {
  const output = new TextDecoder('utf-8', { fatal: true }).decode(git(root, ['ls-tree', '-rlz', '--full-tree', commit]));
  return output.split('\0').filter(Boolean).map(row => {
    const match = /^(\d+) (\w+) ([0-9a-f]+) +(-|\d+)\t([\s\S]+)$/.exec(row);
    if (!match) throw new Error('Invalid Git tree entry');
    return { mode: match[1], type: match[2], oid: match[3], size: Number(match[4]), path: match[5] };
  }).sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
}

function readBlobs(root, entries) {
  const bytes = git(root, ['cat-file', '--batch'], { input: entries.map(entry => entry.oid).join('\n') + '\n' });
  const result = new Map();
  let offset = 0;
  for (const entry of entries) {
    const newline = bytes.indexOf(10, offset);
    const header = bytes.subarray(offset, newline).toString('ascii').split(' ');
    if (header[0] !== entry.oid || header[1] !== 'blob' || Number(header[2]) !== entry.size) throw new Error('Git blob mismatch');
    offset = newline + 1;
    result.set(entry.path, bytes.subarray(offset, offset + entry.size));
    offset += entry.size + 1;
  }
  return result;
}

function assertNoLinks(filename) {
  let current = path.resolve(filename);
  while (true) {
    let stat;
    try { stat = fs.lstatSync(current); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (stat?.isSymbolicLink()) throw new Error(`Linked staging path: ${current}`);
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
}

function assertNewOutput(root, output) {
  const absolute = path.resolve(output);
  const relative = path.relative(root, absolute);
  const inSource = relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
  const inBuild = relative.startsWith(`build${path.sep}`) && relative.split(path.sep).length >= 2;
  const temporaryRelative = path.relative(fs.realpathSync(os.tmpdir()), absolute);
  const inTemporary = temporaryRelative && !temporaryRelative.startsWith('..') && !path.isAbsolute(temporaryRelative);
  if ((inSource && !inBuild) || (!inBuild && !inTemporary)) {
    throw new Error('Output must be a new staging directory under build/ or TEMP, outside source files');
  }
  if (absolute.split(/[\\/]/).some(part => ['configs', 'working', '.git', '.venv', 'python'].includes(part.toLowerCase()))) {
    throw new Error('Protected staging output');
  }
  assertNoLinks(absolute);
  if (fs.existsSync(absolute)) throw new Error('Output already exists; choose a new staging directory');
  return absolute;
}

function exportRuntime({ root = path.resolve(__dirname, '..'), sourceSha, version, policy = 'deploy.personal.txt', output, budget = BUDGET }) {
  root = path.resolve(root);
  validateVersion(version);
  if (!/^[0-9a-f]{40}$/.test(sourceSha) || git(root, ['cat-file', '-t', sourceSha]).toString().trim() !== 'commit') {
    throw new Error('Source SHA must be a full commit SHA, not a tag object or ref');
  }
  const policyBytes = fs.readFileSync(path.resolve(root, policy));
  const allow = parsePolicy(policyBytes);
  const tree = readTree(root, sourceSha);
  const selected = tree.filter(entry => allow.some(name => entry.path === name || entry.path.startsWith(`${name}/`)));
  const seen = new Map();
  for (const entry of selected) {
    validatePath(entry.path);
    if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error(`Unsupported Git file type: ${entry.path}`);
    for (const prefix of entry.path.split('/').map((_, i, parts) => parts.slice(0, i + 1).join('/'))) {
      const key = prefix.toLowerCase();
      if (seen.has(key) && seen.get(key) !== prefix) throw new Error(`Case collision: ${prefix}`);
      seen.set(key, prefix);
    }
  }
  for (const name of REQUIRED) if (!selected.some(entry => entry.path === name)) throw new Error(`Missing required file: ${name}`);
  for (const name of [...RESOURCES, ...allow.filter(name => !REQUIRED.includes(name))]) {
    if (!selected.some(entry => entry.path === name || entry.path.startsWith(`${name}/`))) throw new Error(`Empty required resource: ${name}`);
  }
  const blobs = readBlobs(root, selected);
  const files = selected.map(entry => {
    const bytes = blobs.get(entry.path);
    if (bytes.subarray(0, 128).toString().startsWith('version https://git-lfs.github.com/spec/v1')) throw new Error(`Unresolved LFS pointer: ${entry.path}`);
    return { path: entry.path, size_bytes: bytes.length, sha256: hash(bytes) };
  });
  const total = files.reduce((sum, file) => sum + file.size_bytes, 0);
  const manifest = {
    schema_version: 1, distribution_id: DISTRIBUTION_ID, version,
    source: { repository: SOURCE_REPOSITORY, commit: sourceSha },
    export_policy: { version: 1, sha256: hash(policyBytes) },
    requirements_sha256: hash(blobs.get('requirements.txt')), files, file_count: files.length,
    total_payload_bytes: total, payload_sha256: hash(json(files)),
  };
  const groups = entries => Object.fromEntries([...new Set(entries.map(entry => entry.path.split('/')[0]))].sort()
    .map(name => [name, entries.filter(entry => entry.path.split('/')[0] === name).reduce((sum, entry) => sum + entry.size, 0)]));
  const report = {
    source_commit: sourceSha, version, source_tree_bytes: tree.reduce((sum, entry) => sum + (entry.size || 0), 0),
    source_directories: groups(tree), payload_bytes: total, file_count: files.length,
    payload_directories: groups(selected), largest_files: [...files].sort((a, b) => b.size_bytes - a.size_bytes).slice(0, 20),
    budget_bytes: budget, budget_passed: total <= budget, forbidden_paths_passed: true,
    payload_sha256: manifest.payload_sha256, manifest_sha256: hash(json(manifest)),
    measurements: { git_transfer_bytes: null, python_download_bytes: null, wheel_download_bytes: null,
      cold_install_network_bytes: null, code_update_network_bytes: null, installation_bytes: null },
  };
  const destination = assertNewOutput(root, output);
  fs.mkdirSync(path.join(destination, 'payload'), { recursive: true });
  fs.writeFileSync(path.join(destination, 'runtime-report.json'), json(report));
  if (!report.budget_passed) throw new Error(`Runtime budget exceeded: ${total} > ${budget}; see runtime-report.json`);
  for (const [name, bytes] of blobs) {
    const filename = path.join(destination, 'payload', name);
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, bytes, { flag: 'wx' });
  }
  fs.writeFileSync(path.join(destination, 'payload', MANIFEST), json(manifest), { flag: 'wx' });
  return { manifest, report, directory: path.join(destination, 'payload') };
}

module.exports = { SOURCE_REPOSITORY, DISTRIBUTION_ID, MANIFEST, BUDGET, REQUIRED, RESOURCES,
  hash, json, git, validateVersion, isPrerelease, validatePath, parsePolicy, readTree, readBlobs, assertNoLinks, exportRuntime };
if (require.main === module) {
  try {
    const options = {};
    for (let i = 2; i < process.argv.length; i += 2) {
      const key = { '--source-sha': 'sourceSha', '--version': 'version', '--policy': 'policy', '--output': 'output', '--root': 'root' }[process.argv[i]];
      if (!key || !process.argv[i + 1]) throw new Error('Expected --source-sha SHA --version VERSION --output NEW_DIRECTORY [--policy FILE]');
      options[key] = process.argv[i + 1];
    }
    const result = exportRuntime(options);
    console.log(json({ ...result.report, directory: result.directory }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
