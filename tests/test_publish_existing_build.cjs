const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { validateSource, publishRelease } = require('../.github/scripts/publish_existing_build.cjs');
const { RUNTIME_REPOSITORY } = require('../.github/scripts/publish_personal_runtime.cjs');
const { hash, json } = require('../scripts/export_personal_runtime.cjs');
const { write, sourceFixture, launcherFixture } = require('./personal_runtime_fixture.cjs');

const SHA = 'a'.repeat(40);
const OTHER_SHA = 'b'.repeat(40);
const CONTEXT = { repo: { owner: 'Nancyingcode', repo: 'ok-wuthering-waves' } };
const SOURCE = { version: 'v1.0.4', runId: 123, sha: SHA, artifactId: 1, metadataArtifactId: 2,
  runUrl: 'https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/123', prerelease: false };
function apiFixture() {
  const missing = () => { throw Object.assign(new Error('Not found'), { status: 404 }); };
  const state = { run: { head_sha: SHA, status: 'completed', conclusion: 'success', event: 'workflow_dispatch',
    path: '.github/workflows/build.yml', html_url: SOURCE.runUrl, repository: { full_name: 'Nancyingcode/ok-wuthering-waves' },
    head_repository: { full_name: 'Nancyingcode/ok-wuthering-waves' } }, comparison: 'ahead',
    artifacts: [{ id: 1, name: 'release-assets', expired: false, workflow_run: { head_sha: SHA } },
      { id: 2, name: 'launcher-build-metadata', expired: false, workflow_run: { head_sha: SHA } }],
    release: null, taggedSha: null, assets: [], failUpload: false, failPublic: false };
  const writes = [];
  const github = { rest: { actions: { getWorkflowRun: async () => ({ data: state.run }), listWorkflowRunArtifacts() {} },
    repos: { compareCommits: async () => ({ data: { status: state.comparison } }),
      getReleaseByTag: async () => state.release ? { data: state.release } : missing(),
      getCommit: async () => ({ data: { sha: state.taggedSha } }), listReleaseAssets() {},
      createRelease: async params => { writes.push('draft'); state.release = { ...params, id: 456, body: params.body, html_url: 'https://github.com/example/release' }; return { data: state.release }; },
      uploadReleaseAsset: async params => {
        writes.push(`upload:${params.name}`);
        if (state.failUpload) throw new Error('Upload failed');
        state.assets.push({ id: state.assets.length, name: params.name, size: params.data.length, digest: `sha256:${hash(params.data)}`, data: params.data });
        return { data: state.assets.at(-1) };
      },
      getReleaseAsset: async params => ({ data: state.assets.find(asset => asset.id === params.asset_id).data }),
      updateRelease: async params => { writes.push('public'); if (state.failPublic) throw new Error('Publication failed'); Object.assign(state.release, params); return { data: state.release }; },
    }, git: {
      getRef: async () => state.taggedSha ? { data: { object: { sha: OTHER_SHA, type: 'tag' } } } : missing(),
      createTag: async params => { writes.push('tag'); state.taggedSha = params.object; return { data: { sha: OTHER_SHA } }; },
      createRef: async () => { writes.push('ref'); },
    } }, paginate: async method => method === github.rest.actions.listWorkflowRunArtifacts ? state.artifacts : state.assets };
  return { github, state, writes };
}
const validate = (fixture, inputs = {}) => validateSource({ github: fixture.github, context: CONTEXT,
  version: SOURCE.version, runId: SOURCE.runId, ...inputs });

test('selected successful build retains business SHA and downloads assets plus metadata from that same run', async () => {
  const fixture = apiFixture();
  assert.deepEqual(await validate(fixture), SOURCE);
  assert.deepEqual(fixture.writes, []);
});
for (const [name, change, expected] of [
  ['failed', f => { f.state.run.conclusion = 'failure'; }, /successfully/],
  ['unfinished', f => { f.state.run.status = 'in_progress'; }, /successfully/],
  ['wrong workflow', f => { f.state.run.path = 'other'; }, /Windows Release/],
  ['fork', f => { f.state.run.head_repository.full_name = 'other/repo'; }, /fork or pull request/],
  ['PR', f => { f.state.run.event = 'pull_request'; }, /fork or pull request/],
  ['bad sha', f => { f.state.run.head_sha = 'personal'; }, /invalid commit SHA/],
  ['unmerged', f => { f.state.comparison = 'behind'; }, /merged into personal/],
  ['diverged', f => { f.state.comparison = 'diverged'; }, /merged into personal/],
  ['expired asset', f => { f.state.artifacts[0].expired = true; }, /release-assets/],
  ['missing metadata', f => { f.state.artifacts.pop(); }, /launcher-build-metadata/],
  ['ambiguous metadata', f => { f.state.artifacts.push({ ...f.state.artifacts[1], id: 3 }); }, /launcher-build-metadata/],
  ['expired metadata', f => { f.state.artifacts[1].expired = true; }, /launcher-build-metadata/],
  ['other metadata source', f => { f.state.artifacts[1].workflow_run.head_sha = OTHER_SHA; }, /metadata commit/],
  ['other asset source', f => { f.state.artifacts[0].workflow_run.head_sha = OTHER_SHA; }, /Artifact commit/],
  ['conflicting source tag', f => { f.state.taggedSha = OTHER_SHA; }, /not source build/],
]) {
  test(`${name} refuses all external writes`, async () => {
    const fixture = apiFixture(); change(fixture);
    await assert.rejects(validate(fixture), expected);
    assert.deepEqual(fixture.writes, []);
  });
}
for (const version of ['personal', 'v1.0', 'v1.0.4-preview.foo', 'v1.0.4; echo test', '../v1.0.4']) {
  test(`uninstallable/unsafe version ${version} fails before writes`, async () => {
    const fixture = apiFixture();
    await assert.rejects(validate(fixture, { version }), /Version must/);
    assert.deepEqual(fixture.writes, []);
  });
}
for (const runId of ['-1', '12abc', '1.5', '9007199254740992']) {
  test(`invalid run ID ${runId} is rejected`, async () => await assert.rejects(validate(apiFixture(), { runId }), /Run ID/));
}
for (const status of [403, 422]) {
  test(`reference API ${status} is not treated as missing`, async () => {
    const fixture = apiFixture();
    fixture.github.rest.git.getRef = async () => { throw Object.assign(new Error('lookup failure'), { status }); };
    await assert.rejects(validate(fixture), /lookup failure/);
  });
}
test('missing tag avoids commits API 422; rc prerelease is installable', async () => {
  const fixture = apiFixture();
  fixture.github.rest.repos.getCommit = async () => { throw new Error('must not resolve an absent tag'); };
  assert.equal((await validate(fixture, { version: 'v1.0.4-rc.1' })).prerelease, true);
});

function releaseFixture(t, overrides = {}) {
  const business = sourceFixture(t);
  const source = { ...SOURCE, sha: business.sha, ...overrides };
  const launcher = launcherFixture(business.root, source);
  const staged = business.stage(source.version, source.sha);
  const api = apiFixture();
  let runtimeComplete = false;
  const publish = options => publishRelease({ github: api.github, context: CONTEXT, source, ...launcher,
    staged, root: path.resolve(__dirname, '..'),
    runtimeOptions: { runtimeRepository: RUNTIME_REPOSITORY, runtimeBranch: 'main', token: 'fixture-only' },
    runtimePublisher: async input => {
      api.writes.push('runtime');
      assert.equal(input.businessSourceSha, source.sha);
      assert.notEqual(input.launcherManifest.source.sha, source.sha);
      runtimeComplete = true;
      return { runtimeCommit: 'c'.repeat(40), manifestHash: staged.report.manifest_sha256, phase: 'runtime-confirmed' };
    },
    migrationCheck: async () => { assert.equal(runtimeComplete, true); api.writes.push('smoke'); },
    ...options,
  });
  return { ...api, ...launcher, ...business, source, staged, publish };
}
test('P1/P4: draft assets precede runtime; confirmed runtime and Windows acceptance precede public launcher', async t => {
  const fixture = releaseFixture(t);
  await fixture.publish();
  assert.deepEqual(fixture.writes, ['tag', 'ref', 'draft', 'upload:ok-ww-personal-win32-setup.exe',
    'upload:ok-ww-personal-win32.zip', 'upload:SHA256SUMS.txt', 'runtime', 'smoke', 'public']);
  for (const asset of fixture.state.assets) assert.deepEqual(asset.data, fs.readFileSync(path.join(fixture.directory, asset.name)));
  assert.equal(fixture.state.taggedSha, fixture.source.sha);
});
test('P2: old URL launcher metadata rejects before any tag or release', async t => {
  const fixture = releaseFixture(t);
  fixture.manifest.inputs.prepared.files['launcher/pyappify.personal.yml'] = hash('old URL');
  write(fixture.metadataDirectory, 'manifest.json', json(fixture.manifest));
  await assert.rejects(fixture.publish(), /fingerprint|migration input/);
  assert.deepEqual(fixture.writes, []);
});
test('P4: missing Windows approval retains the complete draft and runtime tag', async t => {
  const fixture = releaseFixture(t);
  const { confirmMigration } = require('../.github/scripts/personal_release_contract.cjs');
  await assert.rejects(fixture.publish({ migrationCheck: parameters => confirmMigration({ ...parameters, approval: '' }) }), /remains draft/);
  assert.equal(fixture.state.release.draft, true);
  assert.equal(fixture.writes.includes('runtime'), true);
  assert.equal(fixture.writes.includes('public'), false);
});
for (const stage of ['upload', 'runtime', 'public']) {
  test(`P4: recover ${stage} failure using matching draft without duplicate or modified assets`, async t => {
    const fixture = releaseFixture(t);
    if (stage === 'upload') fixture.state.failUpload = true;
    if (stage === 'public') fixture.state.failPublic = true;
    const options = stage === 'runtime' ? { runtimePublisher: async () => { throw new Error('Runtime push failed'); } } : {};
    await assert.rejects(fixture.publish(options), /failed/i);
    assert.equal(fixture.state.release.draft, true);
    if (stage === 'upload') assert.equal(fixture.writes.includes('runtime'), false);
    fixture.state.failUpload = false; fixture.state.failPublic = false;
    await fixture.publish();
    assert.equal(fixture.state.release.draft, false);
    assert.equal(fixture.state.assets.length, 3);
    const count = fixture.writes.length;
    await fixture.publish();
    assert.deepEqual(fixture.writes.slice(count), ['runtime']);
  });
}
test('P3: conflicting draft provenance or existing asset digest is preserved and rejected', async t => {
  const fixture = releaseFixture(t);
  fixture.state.failPublic = true;
  await assert.rejects(fixture.publish());
  fixture.state.failPublic = false;
  fixture.state.assets[0].digest = 'sha256:' + '0'.repeat(64);
  await assert.rejects(fixture.publish(), /asset hash conflict/);
  fixture.state.release.body = '<!-- personal-runtime:other -->';
  await assert.rejects(fixture.publish(), /provenance or assets conflict/);
  assert.equal(fixture.state.release.draft, true);
});
test('P5: end-to-end dry-run verifies source/artifacts without calling the GitHub mutation API', async t => {
  const fixture = releaseFixture(t);
  const { publishRuntime } = require('../.github/scripts/publish_personal_runtime.cjs');
  const result = await fixture.publish({ dryRun: true, runtimePublisher: publishRuntime });
  assert.equal(result.externalWrites, false);
  assert.equal(result.businessSourceSha, fixture.source.sha);
  assert.deepEqual(fixture.writes, []);
});
for (const kind of ['missing', 'empty', 'unexpected', 'altered']) {
  test(`${kind} artifacts reject before external writes`, async t => {
    const fixture = releaseFixture(t);
    const installer = path.join(fixture.directory, 'ok-ww-personal-win32-setup.exe');
    if (kind === 'missing') fs.unlinkSync(installer);
    if (kind === 'empty') fs.writeFileSync(installer, '');
    if (kind === 'unexpected') write(fixture.directory, 'private.txt', 'extra');
    if (kind === 'altered') fs.appendFileSync(installer, 'altered');
    await assert.rejects(fixture.publish());
    assert.deepEqual(fixture.writes, []);
  });
}
