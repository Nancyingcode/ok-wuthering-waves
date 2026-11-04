const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { execFileSync } = require('node:child_process');
const { hash, json, parsePolicy, validatePath, validateVersion, exportRuntime } = require('../scripts/export_personal_runtime.cjs');
const { git, write, sourceFixture } = require('./personal_runtime_fixture.cjs');

test('E1/E2/E3: immutable, recursive binary export ignores dirty and untracked workspace contents', t => {
  const fixture = sourceFixture(t);
  const before = fixture.stage();
  write(fixture.root, 'requirements.txt', 'CI inlined dependencies');
  write(fixture.root, 'configs/custom_teams/private.py', 'private');
  write(fixture.root, 'assets/secret-untracked.bin', 'not tracked');
  const after = fixture.stage();
  assert.deepEqual(after.manifest, before.manifest);
  assert.equal(hash(fs.readFileSync(path.join(before.directory, 'runtime-manifest.json'))), after.report.manifest_sha256);
  assert.equal(after.manifest.files.some(file => file.path === 'src/web/character_code/index.js'), true);
  assert.equal(after.manifest.files.some(file => /secret|configs|tests|ok_templates|custom_teams/.test(file.path)), false);
  assert.equal(fs.readFileSync(path.join(after.directory, 'requirements.txt'), 'utf8'), 'original requirements.txt\r\n');
  for (const file of after.manifest.files) {
    const data = fs.readFileSync(path.join(after.directory, file.path));
    assert.equal(data.length, file.size_bytes);
    assert.equal(hash(data), file.sha256);
  }
  assert.equal(after.manifest.payload_sha256, hash(json(after.manifest.files)));
});

for (const value of ['/absolute', '../escape', 'assets/../secret', 'C:/private', 'src\\file', 'assets/NUL.png', 'assets/a.', 'configs', 'src/node_modules', 'custom_teams']) {
  test(`E4: unsafe/forbidden policy rejects ${value}`, () => assert.throws(() => parsePolicy(Buffer.from(value))));
}
test('E4: duplicate policy paths and manifest injection fail', () => {
  assert.throws(() => parsePolicy(Buffer.from('assets\nASSETS')));
  assert.throws(() => parsePolicy(Buffer.from('runtime-manifest.json')));
  assert.throws(() => validatePath('assets/line\nbreak'));
});
for (const [name, mode, content, error] of [
  ['symlink', '120000', '../outside', /Unsupported/],
  ['LFS', '100644', 'version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 10\n', /LFS/],
]) {
  test(`E4: ${name} Git object cannot enter payload`, t => {
    const fixture = sourceFixture(t);
    const oid = execFileSync('git', ['-C', fixture.root, 'hash-object', '-w', '--stdin'], { input: content, encoding: 'utf8' }).trim();
    git(fixture.root, 'update-index', '--add', '--cacheinfo', `${mode},${oid},assets/unsafe`);
    git(fixture.root, 'commit', '-m', name);
    assert.throws(() => fixture.stage('v1.0.4', git(fixture.root, 'rev-parse', 'HEAD')), error);
  });
}
test('E4: submodule, case conflicts, annotated tag object and missing resource fail', t => {
  const fixture = sourceFixture(t);
  git(fixture.root, 'tag', '-a', 'v9.9.9', '-m', 'annotated');
  assert.throws(() => fixture.stage('v1.0.4', git(fixture.root, 'rev-parse', 'v9.9.9')), /full commit/);
  git(fixture.root, 'update-index', '--add', '--cacheinfo', `160000,${fixture.sha},assets/submodule`);
  git(fixture.root, 'commit', '-m', 'submodule');
  assert.throws(() => fixture.stage('v1.0.4', git(fixture.root, 'rev-parse', 'HEAD')), /Unsupported/);
  const oid = git(fixture.root, 'rev-parse', `${fixture.sha}:src/fixture.bin`);
  git(fixture.root, 'update-index', '--force-remove', 'assets/submodule');
  git(fixture.root, 'update-index', '--add', '--cacheinfo', `100644,${oid},src/Web/other.bin`);
  git(fixture.root, 'commit', '-m', 'case collision');
  assert.throws(() => fixture.stage('v1.0.4', git(fixture.root, 'rev-parse', 'HEAD')), /Case collision/);
  git(fixture.root, 'update-index', '--force-remove', 'src/Web/other.bin', 'main.py');
  git(fixture.root, 'commit', '-m', 'missing entry');
  assert.throws(() => fixture.stage('v1.0.4', git(fixture.root, 'rev-parse', 'HEAD')), /Missing required/);
});
test('E4: staging refuses existing/source/protected/linked output, budget writes a diagnostic report', t => {
  const fixture = sourceFixture(t);
  for (const output of [fixture.root, path.join(fixture.root, 'src/staging'), path.join(fixture.root, 'configs/new')]) {
    assert.throws(() => fixture.stage('v1.0.4', fixture.sha, { output }));
  }
  const first = fixture.stage();
  assert.throws(() => fixture.stage('v1.0.4', fixture.sha, { output: path.dirname(first.directory) }), /already exists/);
  const linked = path.join(fixture.root, 'build/linked');
  fs.symlinkSync(path.dirname(first.directory), linked, 'junction');
  assert.throws(() => fixture.stage('v1.0.4', fixture.sha, { output: path.join(linked, 'new') }), /Linked/);
  const output = path.join(fixture.root, 'build/over-budget');
  assert.throws(() => fixture.stage('v1.0.4', fixture.sha, { output, budget: 1 }), /budget exceeded/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(output, 'runtime-report.json'))).budget_passed, false);
});
test('E5: deleting a source file removes it, identical business files still map a new version/source', t => {
  const fixture = sourceFixture(t);
  const first = fixture.stage();
  fs.unlinkSync(path.join(fixture.root, 'src/web/character_code/index.js'));
  const nextSha = fixture.commit();
  const second = fixture.stage('v1.0.5', nextSha);
  assert.equal(fs.existsSync(path.join(second.directory, 'src/web/character_code/index.js')), false);
  const third = fixture.stage('v1.0.6', nextSha);
  assert.equal(third.manifest.payload_sha256, second.manifest.payload_sha256);
  assert.notEqual(third.report.manifest_sha256, second.report.manifest_sha256);
  assert.notEqual(first.manifest.payload_sha256, second.manifest.payload_sha256);
});
test('version syntax follows pinned client, including rc and dotted legacy prereleases', () => {
  for (const version of ['v1.0.4', 'v1.0.4-beta.1', 'v1.0.4-rc.1', '1.2.3.alpha', 'v1.2.3-beta']) assert.equal(validateVersion(version), version);
  for (const version of ['v1.0.4-preview.foo', 'v1.2', '../v1.2.3', 'v1.2.3; echo x', 'v18446744073709551616.0.0']) assert.throws(() => validateVersion(version));
});
