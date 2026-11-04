const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { SOURCE_REPOSITORY, DISTRIBUTION_ID, MANIFEST, BUDGET, REQUIRED, RESOURCES,
  hash, json, validateVersion, validatePath, readTree, readBlobs, assertNoLinks } = require('../../scripts/export_personal_runtime.cjs');

const RUNTIME_REPOSITORY = 'Nancyingcode/ok-ww-personal-update';
const RUNTIME_URL = `https://github.com/${RUNTIME_REPOSITORY}.git`;
const RUNTIME_COMMIT_IDENTITY = {
  name: 'Nancyingcode',
  email: '29879289+Nancyingcode@users.noreply.github.com',
};

function validateConfiguration(repository, branch, root = path.resolve(__dirname, '../..')) {
  if (repository !== RUNTIME_REPOSITORY) throw new Error(`PERSONAL_RUNTIME_REPOSITORY must be ${RUNTIME_REPOSITORY}`);
  if (!/^[A-Za-z0-9][A-Za-z0-9/_-]*$/.test(branch) || branch.endsWith('/') || branch.includes('//')) throw new Error('Invalid runtime branch');
  const yaml = fs.readFileSync(path.join(root, 'launcher/pyappify.personal.yml'), 'utf8');
  const personal = yaml.split(/- name: "Personal"/)[1];
  if (!personal || !personal.includes(`git_url: "${RUNTIME_URL}"`)) throw new Error('Runtime repository and embedded Personal URL differ');
}

function validateManifest(manifest) {
  validateVersion(manifest.version);
  const hasIdentity = manifest.schema_version === 1 && manifest.distribution_id === DISTRIBUTION_ID &&
    manifest.source?.repository === SOURCE_REPOSITORY && /^[0-9a-f]{40}$/.test(manifest.source.commit) &&
    manifest.export_policy?.version === 1 && /^[0-9a-f]{64}$/.test(manifest.export_policy.sha256);
  if (!hasIdentity || !Array.isArray(manifest.files)) throw new Error('Invalid runtime manifest identity');
  const paths = new Map();
  for (const file of manifest.files) {
    validatePath(file.path);
    if (file.path === MANIFEST || path.basename(file.path).toLowerCase() === '.gitattributes') throw new Error('Reserved runtime file / Git attributes');
    if (!Number.isSafeInteger(file.size_bytes) || file.size_bytes < 0 || !/^[0-9a-f]{64}$/.test(file.sha256)) throw new Error('Invalid runtime file record');
    for (const prefix of file.path.split('/').map((_, i, parts) => parts.slice(0, i + 1).join('/'))) {
      const key = prefix.toLowerCase();
      if (paths.has(key) && paths.get(key) !== prefix) throw new Error('Runtime case collision');
      paths.set(key, prefix);
    }
  }
  const names = manifest.files.map(file => file.path);
  const sorted = [...names].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)));
  const validFiles = new Set(names).size === names.length && json(names) === json(sorted) &&
    manifest.file_count === names.length && manifest.total_payload_bytes <= BUDGET &&
    manifest.total_payload_bytes === manifest.files.reduce((sum, file) => sum + file.size_bytes, 0) &&
    manifest.payload_sha256 === hash(json(manifest.files));
  if (!validFiles) throw new Error('Runtime manifest file set/hash/budget mismatch');
  for (const name of REQUIRED) if (!names.includes(name)) throw new Error(`Missing runtime file: ${name}`);
  for (const name of RESOURCES) if (!names.some(file => file.startsWith(`${name}/`))) throw new Error(`Missing runtime resources: ${name}`);
  if (manifest.requirements_sha256 !== manifest.files.find(file => file.path === 'requirements.txt').sha256) throw new Error('Requirements hash mismatch');
}

function loadStage(directory, expectedHash) {
  assertNoLinks(directory);
  const bytes = fs.readFileSync(path.join(directory, MANIFEST));
  if (hash(bytes) !== expectedHash) throw new Error('Staged runtime manifest hash mismatch');
  const manifest = JSON.parse(bytes);
  validateManifest(manifest);
  const names = [];
  const walk = (folder, relative = '') => {
    for (const name of fs.readdirSync(folder)) {
      const filename = path.join(folder, name);
      const next = relative ? `${relative}/${name}` : name;
      const stat = fs.lstatSync(filename);
      if (stat.isSymbolicLink()) throw new Error('Linked runtime stage member');
      if (stat.isDirectory()) walk(filename, next);
      else if (stat.isFile()) names.push(next);
      else throw new Error('Unsupported runtime stage member');
    }
  };
  walk(directory);
  const expected = [...manifest.files.map(file => file.path), MANIFEST].sort();
  if (json(names.sort()) !== json(expected)) throw new Error('Staged runtime file set mismatch');
  for (const file of manifest.files) {
    const data = fs.readFileSync(path.join(directory, file.path));
    if (data.length !== file.size_bytes || hash(data) !== file.sha256) throw new Error(`Staged runtime bytes mismatch: ${file.path}`);
  }
  return manifest;
}

function gitEnvironment(token, emptyConfig) {
  // Ignore inherited Git filters, templates, hooks and injected config. Authentication
  // stays in this process environment and is never persisted in a remote URL.
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: emptyConfig, GIT_ATTR_NOSYSTEM: '1', GIT_TERMINAL_PROMPT: '0' });
  if (token) Object.assign(env, { GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'http.https://github.com/.extraheader',
    GIT_CONFIG_VALUE_0: `AUTHORIZATION: basic ${Buffer.from(`x-access-token:${token}`).toString('base64')}` });
  return env;
}

function verifyCommit(root, commit, expectedManifestHash) {
  const tree = readTree(root, commit);
  if (tree.some(entry => entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode))) throw new Error('Unsupported runtime Git tree');
  const manifestEntry = tree.find(entry => entry.path === MANIFEST);
  if (!manifestEntry) throw new Error('Remote/runtime manifest conflict');
  const manifestBytes = readBlobs(root, [manifestEntry]).get(MANIFEST);
  if (!manifestBytes || (expectedManifestHash && hash(manifestBytes) !== expectedManifestHash)) throw new Error('Remote/runtime manifest conflict');
  const manifest = JSON.parse(manifestBytes);
  validateManifest(manifest);
  const expected = [...manifest.files.map(file => file.path), MANIFEST].sort();
  if (json(tree.map(entry => entry.path).sort()) !== json(expected)) throw new Error('Runtime commit file set mismatch');
  const sizes = new Map(tree.map(entry => [entry.path, entry.size]));
  if (manifest.files.some(file => sizes.get(file.path) !== file.size_bytes)) throw new Error('Runtime Git blob size mismatch');
  const blobs = readBlobs(root, tree);
  for (const file of manifest.files) {
    const data = blobs.get(file.path);
    if (data.length !== file.size_bytes || hash(data) !== file.sha256) throw new Error(`Runtime commit bytes mismatch: ${file.path}`);
  }
  return { manifest, manifestHash: hash(manifestBytes), tree: execFileSync('git', ['-C', root, 'rev-parse', `${commit}^{tree}`], { encoding: 'utf8' }).trim() };
}

function publishRuntime({ version, businessSourceSha, sourceBuildRunId, runtimeRepository, runtimeBranch,
  stagedRuntimeDir, runtimeManifestHash, launcherManifest, releaseAssetHashes,
  token, dryRun = false, localRemote, beforePush, requireExistingTag = false, root = path.resolve(__dirname, '../..') }) {
  validateConfiguration(runtimeRepository, runtimeBranch, root);
  validateVersion(version);
  const manifest = loadStage(stagedRuntimeDir, runtimeManifestHash);
  if (manifest.version !== version || manifest.source.commit !== businessSourceSha) throw new Error('Business source/version does not match runtime stage');
  const receipt = { version, businessSourceSha, sourceBuildRunId, runtimeRepository, runtimeBranch,
    exportPolicy: manifest.export_policy, payloadHash: manifest.payload_sha256, manifestHash: runtimeManifestHash,
    launcherSource: launcherManifest?.source, releaseAssetHashes, phase: 'validated', retryable: true };
  if (dryRun) return { ...receipt, phase: 'dry-run', externalWrites: false };
  if (!localRemote && !token) throw new Error('Missing PERSONAL_RUNTIME_TOKEN for cross-repository publication');
  const remote = localRemote ? path.resolve(localRemote) : `git@github.com:${runtimeRepository}.git`;
  // CI uses HTTPS authentication, while all stored Git remotes use credential-free SSH.
  const transport = localRemote ? remote : RUNTIME_URL;
  const temporary = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'personal-runtime-publish-'));
  // Git for Windows does not accept Node's \\.\nul as a config filename.
  const emptyConfig = path.join(temporary, 'empty-config');
  fs.writeFileSync(emptyConfig, '');
  const env = gitEnvironment(token, emptyConfig);
  const run = args => execFileSync('git', ['-C', temporary, ...args], { env, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
  try {
    run(['init', '--template=', '--initial-branch', runtimeBranch]);
    fs.renameSync(emptyConfig, path.join(temporary, '.git/empty-config'));
    env.GIT_CONFIG_GLOBAL = path.join(temporary, '.git/empty-config');
    for (const [key, value] of Object.entries({ 'core.autocrlf': 'false', 'core.longpaths': 'true',
      'core.attributesfile': env.GIT_CONFIG_GLOBAL, 'core.hooksPath': path.join(temporary, '.git/disabled-hooks'),
      'user.name': RUNTIME_COMMIT_IDENTITY.name, 'user.email': RUNTIME_COMMIT_IDENTITY.email })) run(['config', '--local', key, value]);
    run(['remote', 'add', 'origin', remote]);
    const verifiedHistory = new Set();
    for (let attempt = 0; attempt < 3; attempt++) {
      const refs = run(['ls-remote', '--refs', transport]).split('\n').filter(Boolean).map(line => line.split(/\s+/));
      if (refs.some(([, ref]) => ref !== `refs/heads/${runtimeBranch}` && !ref.startsWith('refs/tags/'))) throw new Error('Unexpected refs in runtime repository');
      if (refs.length && !refs.some(([, ref]) => ref === `refs/heads/${runtimeBranch}`)) throw new Error('Runtime branch missing in nonempty repository');
      for (const [, ref] of refs.filter(([, ref]) => ref.startsWith('refs/tags/'))) validateVersion(ref.slice('refs/tags/'.length));
      if (refs.length) {
        // The client fetches all tags. A development tag outside the release branch
        // would still bring its history to users, so verify every advertised history.
        run(['fetch', '--no-tags', transport, ...refs.map(([, ref]) => `+${ref}:refs/validated/${ref.slice(5)}`)]);
        for (const commit of run(['rev-list', '--all']).split('\n')) {
          if (!verifiedHistory.has(commit)) {
            verifyCommit(temporary, commit);
            verifiedHistory.add(commit);
          }
        }
      }
      const existingTag = refs.find(([, ref]) => ref === `refs/tags/${version}`);
      if (existingTag) {
        const ref = `refs/validated/tags/${version}`;
        const verified = verifyCommit(temporary, ref, runtimeManifestHash);
        return { ...receipt, manifestHash: verified.manifestHash, runtimeCommit: run(['rev-parse', `${ref}^{commit}`]), runtimeTree: verified.tree, phase: 'runtime-confirmed', retryable: false };
      }
      if (requireExistingTag) throw new Error('Published source release is missing its matching runtime tag');
      const branch = refs.find(([, ref]) => ref === `refs/heads/${runtimeBranch}`);
      let parent;
      if (branch) {
        parent = run(['rev-parse', `refs/validated/heads/${runtimeBranch}`]);
      } else if (refs.length) throw new Error('Runtime branch missing in nonempty repository');
      run(['read-tree', '--empty']);
      for (const name of [...manifest.files.map(file => file.path), MANIFEST]) {
        const destination = path.join(temporary, name);
        fs.mkdirSync(path.dirname(destination), { recursive: true });
        fs.copyFileSync(path.join(stagedRuntimeDir, name), destination);
      }
      run(['add', '--all', '--', '.']);
      const tree = run(['write-tree']);
      const commit = run(['commit-tree', tree, ...(parent ? ['-p', parent] : []), '-m', `发布个人运行版本 ${version}\n业务源码：${businessSourceSha}`]);
      verifyCommit(temporary, commit, runtimeManifestHash);
      if (beforePush) beforePush(attempt);
      try {
        run(['push', '--atomic', transport, `${commit}:refs/heads/${runtimeBranch}`, `${commit}:refs/tags/${version}`]);
      } catch (error) {
        // Only retry a moved branch/tag. Authentication and lack of atomic push
        // support must fail without degrading to a partially published version.
        const updated = run(['ls-remote', '--refs', transport]);
        const prior = refs.map(row => row.join('\t')).join('\n');
        if (attempt === 2 || updated === prior) throw new Error('Atomic runtime push failed; release remains draft', { cause: error });
        run(['read-tree', '--empty']);
        // Each retry uses the same controlled payload; no remote working tree is copied.
        continue;
      }
      run(['fetch', '--no-tags', transport, `refs/tags/${version}`]);
      const confirmed = verifyCommit(temporary, 'FETCH_HEAD', runtimeManifestHash);
      const actualCommit = run(['rev-parse', 'FETCH_HEAD^{commit}']);
      if (actualCommit !== commit || confirmed.tree !== tree) throw new Error('Remote runtime tree/commit mismatch');
      return { ...receipt, manifestHash: confirmed.manifestHash, runtimeCommit: actualCommit, runtimeTree: tree, phase: 'runtime-confirmed', retryable: false };
    }
  } finally {
    const base = fs.realpathSync(os.tmpdir());
    if (path.dirname(temporary) !== base || !path.basename(temporary).startsWith('personal-runtime-publish-')) throw new Error('Unsafe publisher cleanup');
    assertNoLinks(temporary);
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

module.exports = { RUNTIME_REPOSITORY, RUNTIME_URL, validateConfiguration, validateManifest, loadStage, verifyCommit, publishRuntime };
if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length !== 2 || args[0] !== '--dry-run') throw new Error('Usage: node .github/scripts/publish_personal_runtime.cjs --dry-run STAGED_PAYLOAD');
    const directory = path.resolve(args[1]);
    const bytes = fs.readFileSync(path.join(directory, MANIFEST));
    const manifest = JSON.parse(bytes);
    console.log(json({ ...publishRuntime({ version: manifest.version, businessSourceSha: manifest.source.commit,
      runtimeRepository: RUNTIME_REPOSITORY, runtimeBranch: process.env.PERSONAL_RUNTIME_BRANCH || 'main',
      stagedRuntimeDir: directory, runtimeManifestHash: hash(bytes), dryRun: true }),
    launcherValidation: 'not-performed: requires an actual compatible build and same-run launcher metadata' }));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
