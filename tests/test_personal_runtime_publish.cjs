const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { publishRuntime, verifyCommit, RUNTIME_REPOSITORY } = require('../.github/scripts/publish_personal_runtime.cjs');
const { validateLauncher, confirmMigration } = require('../.github/scripts/personal_release_contract.cjs');
const { hash, json } = require('../scripts/export_personal_runtime.cjs');
const { ROOT, git, write, temporary, sourceFixture, launcherFixture } = require('./personal_runtime_fixture.cjs');
const { fingerprint, buildIdentity, fileHashes } = require('../scripts/personal_launcher_cache.cjs');

function publishing(t) {
  const fixture = sourceFixture(t);
  const remote = temporary(t);
  git(remote, 'init', '--bare', '--template=');
  const publish = (staged = fixture.stage(), overrides = {}) => publishRuntime({
    version: staged.manifest.version, businessSourceSha: staged.manifest.source.commit, sourceBuildRunId: 123,
    runtimeRepository: RUNTIME_REPOSITORY, runtimeBranch: 'main', stagedRuntimeDir: staged.directory,
    runtimeManifestHash: staged.report.manifest_sha256, localRemote: remote, root: ROOT, ...overrides,
  });
  return { ...fixture, remote, publish };
}

test('fresh runtime history excludes the development commit and confirms published bytes', t => {
  const fixture = publishing(t);
  const first = fixture.stage();
  const published = fixture.publish(first);
  assert.equal(published.phase, 'runtime-confirmed');
  assert.equal(git(fixture.remote, 'rev-list', '--count', '--all'), '1');
  assert.throws(() => git(fixture.remote, 'cat-file', '-t', fixture.sha), /Command failed/);
});

test('E6: inherited CRLF and clean filter settings cannot alter published Git bytes', t => {
  const fixture = publishing(t);
  const config = path.join(fixture.root, 'global-gitconfig');
  const attributes = path.join(fixture.root, 'attributes');
  write(fixture.root, 'attributes', '* text eol=crlf filter=broken\n');
  write(fixture.root, 'global-gitconfig', `[core]\n autocrlf = true\n attributesfile = ${attributes.replaceAll('\\', '/')}\n[filter "broken"]\n clean = false\n required = true\n`);
  const previous = process.env.GIT_CONFIG_GLOBAL;
  try {
    process.env.GIT_CONFIG_GLOBAL = config;
    const stage = fixture.stage();
    const result = fixture.publish(stage);
    assert.equal(result.manifestHash, stage.report.manifest_sha256);
    assert.equal(git(fixture.remote, 'show', 'v1.0.4:requirements.txt'), 'original requirements.txt');
    assert.equal(verifyCommit(fixture.remote, 'v1.0.4', stage.report.manifest_sha256).manifest.payload_sha256, stage.manifest.payload_sha256);
  } finally {
    if (previous === undefined) delete process.env.GIT_CONFIG_GLOBAL; else process.env.GIT_CONFIG_GLOBAL = previous;
  }
});

test('E5/P3: tags are idempotent, different source/policy conflicts, deleted files stay absent', t => {
  const fixture = publishing(t);
  const first = fixture.stage();
  const result = fixture.publish(first);
  assert.equal(fixture.publish(first).runtimeCommit, result.runtimeCommit);
  assert.equal(git(fixture.remote, 'rev-list', '--count', '--all'), '1');
  fs.unlinkSync(path.join(fixture.root, 'src/web/character_code/index.js'));
  const sha = fixture.commit();
  assert.throws(() => fixture.publish(fixture.stage('v1.0.4', sha)), /manifest conflict/);
  const next = fixture.stage('v1.0.5', sha);
  fixture.publish(next);
  const same = fixture.stage('v1.0.6', sha);
  fixture.publish(same);
  assert.equal(same.manifest.payload_sha256, next.manifest.payload_sha256);
  assert.notEqual(same.report.manifest_sha256, next.report.manifest_sha256);
  assert.equal(git(fixture.remote, 'rev-list', '--count', '--all'), '3');
  assert.equal(git(fixture.remote, 'ls-tree', '-r', '--name-only', 'v1.0.5').includes('src/web/character_code/index.js'), false);
  write(fixture.root, 'deploy.personal.txt', fs.readFileSync(path.join(fixture.root, 'deploy.personal.txt')) + '# new policy identity\n');
  assert.throws(() => fixture.publish(fixture.stage('v1.0.6', sha)), /manifest conflict/);
});

test('P6: competing different versions rebase on the new runtime parent without losing either tag', t => {
  const fixture = publishing(t);
  const first = fixture.stage('v1.0.4');
  const second = fixture.stage('v1.0.5');
  const result = fixture.publish(first, { beforePush: attempt => {
    if (attempt === 0) fixture.publish(second);
  } });
  assert.equal(result.phase, 'runtime-confirmed');
  assert.equal(git(fixture.remote, 'rev-list', '--count', 'main'), '2');
  assert.equal(git(fixture.remote, 'tag', '--list').split('\n').length, 2);
});
test('P3/P6: a competing same version with different source is never overwritten', t => {
  const fixture = publishing(t);
  const first = fixture.stage();
  const otherSha = fixture.commit();
  const second = fixture.stage('v1.0.4', otherSha);
  assert.throws(() => fixture.publish(first, { beforePush: attempt => { if (attempt === 0) fixture.publish(second); } }), /manifest conflict/);
  assert.equal(verifyCommit(fixture.remote, 'v1.0.4').manifest.source.commit, otherSha);
});
test('P4: a server rejecting atomic push leaves no branch or tag', t => {
  const fixture = publishing(t);
  git(fixture.remote, 'config', 'receive.advertiseAtomic', 'false');
  assert.throws(() => fixture.publish(), /Atomic runtime push failed/);
  assert.equal(fs.existsSync(path.join(fixture.remote, 'refs/heads/main')), false);
  assert.equal(fs.existsSync(path.join(fixture.remote, 'refs/tags/v1.0.4')), false);
});
test('P5: dry-run has no remote writes; drift, unsafe branch and source contamination fail before push', t => {
  const fixture = publishing(t);
  const stage = fixture.stage();
  assert.equal(fixture.publish(stage, { dryRun: true }).externalWrites, false);
  assert.equal(fs.existsSync(path.join(fixture.remote, 'refs/heads/main')), false);
  for (const overrides of [{ runtimeRepository: 'Nancyingcode/ok-wuthering-waves' }, { runtimeBranch: '../main' }, { businessSourceSha: 'f'.repeat(40) }]) {
    assert.throws(() => fixture.publish(stage, overrides));
  }
  fs.appendFileSync(path.join(stage.directory, 'requirements.txt'), 'polluted');
  assert.throws(() => fixture.publish(stage), /bytes mismatch/);
});
test('development history hidden under an unrelated tag is rejected before runtime publication', t => {
  const fixture = publishing(t);
  fixture.publish(fixture.stage());
  git(fixture.root, 'push', fixture.remote, `${fixture.sha}:refs/tags/v9.9.9`);
  const original = git(fixture.remote, 'rev-parse', 'main');
  assert.throws(() => fixture.publish(fixture.stage('v1.0.5')), /manifest conflict/);
  assert.equal(git(fixture.remote, 'rev-parse', 'main'), original);
});
test('P2/C1: launcher metadata binds selected business run to compatible cached artifact provenance', t => {
  const fixture = sourceFixture(t);
  const source = { sha: fixture.sha, runId: 123, runUrl: 'https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/123' };
  const launcher = launcherFixture(fixture.root, source);
  assert.notEqual(launcher.manifest.source.sha, source.sha);
  assert.deepEqual(validateLauncher({ ...launcher, source }), launcher.manifest);
  assert.throws(() => validateLauncher({ ...launcher, source: { ...source, runId: 124 } }), /selected business build run/);
  const metadata = path.join(launcher.metadataDirectory, 'manifest.json');
  launcher.manifest.inputs.prepared.files['launcher/pyappify.personal.yml'] = '0'.repeat(64);
  const old = fingerprint({ ...launcher.manifest.inputs, tools: launcher.manifest.buildTools });
  launcher.manifest.fingerprint = old.fingerprint;
  launcher.manifest.launcherTest.fingerprint = old.fingerprint;
  launcher.manifest.launcherTest.buildFingerprint = buildIdentity(old);
  write(launcher.metadataDirectory, 'manifest.json', json(launcher.manifest));
  assert.throws(() => validateLauncher({ ...launcher, source }), /migration input/);
  assert.equal(fs.existsSync(metadata), true);
});
test('first migration cannot publish a launcher without real Windows acceptance', async () => {
  await assert.rejects(confirmMigration({ approval: '', runtime: {}, launcherManifest: {} }), /Windows cold-install/);
  await assert.rejects(confirmMigration({ approval: json({ version: 'v1.0.4' }), runtime: {}, launcherManifest: {} }), /incomplete/);
});
test('migration acceptance must match a remote stable runtime and the exact tested launcher artifacts', async t => {
  const fixture = sourceFixture(t);
  const source = { sha: fixture.sha, runId: 123, runUrl: 'https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/123' };
  const launcher = launcherFixture(fixture.root, source);
  const stage = fixture.stage();
  const bytes = fs.readFileSync(path.join(stage.directory, 'runtime-manifest.json'));
  const approval = { runtimeRepository: RUNTIME_REPOSITORY, version: 'v1.0.4', runtimeCommit: 'c'.repeat(40),
    businessSourceSha: fixture.sha, manifestHash: hash(bytes), payloadHash: stage.manifest.payload_sha256,
    inputs: fileHashes(ROOT, ['launcher/pyappify.personal.yml', 'launcher/pyappify-profiles.patch']),
    launcherArtifacts: launcher.manifest.artifacts,
    checks: Object.fromEntries(['coldInstall', 'pipCheck', 'imports', 'gui', 'templates', 'ocr', 'echo', 'sourceSwitch', 'userData'].map(key => [key, true])),
    evidenceUrl: 'https://github.com/example/acceptance' };
  let remoteCommit = approval.runtimeCommit;
  const github = { rest: { repos: { getCommit: async () => ({ data: { sha: remoteCommit } }),
    getContent: async () => ({ data: { encoding: 'base64', content: bytes.toString('base64') } }) } } };
  const check = () => confirmMigration({ github, approval: json(approval), runtime: { runtimeCommit: 'd'.repeat(40) }, launcherManifest: launcher.manifest });
  assert.equal((await check()).version, 'v1.0.4');
  remoteCommit = 'e'.repeat(40);
  await assert.rejects(check(), /remote stable runtime/);
  remoteCommit = approval.runtimeCommit;
  approval.launcherArtifacts = [];
  await assert.rejects(check(), /incomplete or belongs/);
});
