const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { exportRuntime, REQUIRED, RESOURCES, hash } = require('../scripts/export_personal_runtime.cjs');
const { fingerprint, fileHashes, SETTINGS, SCHEMA, RELEASE_FILES, buildIdentity } = require('../scripts/personal_launcher_cache.cjs');
const ROOT = path.resolve(__dirname, '..');
const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
function write(root, name, bytes) {
  fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
  fs.writeFileSync(path.join(root, name), bytes);
}
function temporary(t) {
  const base = fs.realpathSync(os.tmpdir());
  const root = fs.mkdtempSync(path.join(base, 'personal-runtime-test-'));
  t.after(() => {
    assert.equal(path.dirname(root), base);
    assert.ok(path.basename(root).startsWith('personal-runtime-test-'));
    fs.rmSync(root, { recursive: true, force: true });
  });
  return root;
}
function sourceFixture(t) {
  const root = temporary(t);
  git(root, 'init', '--template=', '--initial-branch=main');
  git(root, 'config', 'core.autocrlf', 'false');
  git(root, 'config', 'user.name', 'Runtime Test');
  git(root, 'config', 'user.email', 'test@example.com');
  for (const name of REQUIRED) write(root, name, `original ${name}\r\n`);
  for (const name of RESOURCES) write(root, `${name}/fixture.bin`, Buffer.from([0, 13, 10, 255]));
  write(root, 'src/web/character_code/index.js', 'window.fixture = true;\n');
  write(root, 'assets/templates/中文.png', Buffer.from([0, 128, 255]));
  for (const name of ['ok_templates', 'tests', 'docs', 'configs', 'custom_teams']) write(root, `${name}/private.txt`, 'excluded');
  write(root, 'deploy.personal.txt', [...REQUIRED, ...RESOURCES].join('\n') + '\n');
  const commit = () => { git(root, 'add', '--all'); git(root, 'commit', '--allow-empty', '-m', 'fixture'); return git(root, 'rev-parse', 'HEAD'); };
  const sha = commit();
  let counter = 0;
  const stage = (version = 'v1.0.4', sourceSha = sha, options = {}) => exportRuntime({ root, version, sourceSha,
    output: path.join(root, 'build', `export-${counter++}`), ...options });
  return { root, sha, commit, stage };
}
function launcherFixture(root, source) {
  const directory = path.join(root, 'release-assets');
  const metadataDirectory = path.join(root, 'launcher-metadata');
  const artifacts = RELEASE_FILES.map(name => {
    const data = Buffer.from(`original binary: ${name}`);
    write(directory, name, data);
    return { name, bytes: data.length, sha256: hash(data) };
  });
  const data = fingerprint({ prepared: { files: fileHashes(ROOT, ['launcher/pyappify.personal.yml', 'launcher/pyappify-profiles.patch']) }, tools: {
    node: 'old-node', pnpm: 'old-pnpm', rustc: 'old-rustc', cargo: 'old-cargo', powershell: 'old-pwsh',
    msvcInstallation: 'old-msvc', os: 'win32', imageOS: 'old-image', imageVersion: 'old-version',
  } });
  const manifest = { schema: SCHEMA, fingerprint: data.fingerprint, inputs: data.inputs, buildTools: data.tools,
    upstreamCommit: SETTINGS.commit, source: { repository: 'Nancyingcode/ok-wuthering-waves', sha: 'b'.repeat(40), runId: 12 },
    launcherTest: { name: 'launcher_profiles', passed: true, fingerprint: data.fingerprint, buildFingerprint: buildIdentity(data) }, artifacts };
  write(metadataDirectory, 'manifest.json', JSON.stringify(manifest));
  write(metadataDirectory, 'report.json', JSON.stringify({ currentSha: source.sha, runUrl: source.runUrl }));
  return { directory, metadataDirectory, manifest };
}
module.exports = { ROOT, git, write, temporary, sourceFixture, launcherFixture };
