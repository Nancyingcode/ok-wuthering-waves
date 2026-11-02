const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { validateSource, publishRelease } = require('../.github/scripts/publish_existing_build.cjs');

const SHA = 'a'.repeat(40);
const OTHER_SHA = 'b'.repeat(40);
const CONTEXT = { repo: { owner: 'Nancyingcode', repo: 'ok-wuthering-waves' } };
const SOURCE = {
  version: 'v1.0.2', runId: 41366113840, sha: SHA, artifactId: 123,
  runUrl: 'https://github.com/Nancyingcode/ok-wuthering-waves/actions/runs/41366113840',
  prerelease: false,
};

function apiFixture(overrides = {}) {
  const notFound = () => { throw Object.assign(new Error('Not found'), { status: 404 }); };
  const state = {
    run: {
      head_sha: SHA, status: 'completed', conclusion: 'success', event: 'workflow_dispatch',
      path: '.github/workflows/build.yml', html_url: SOURCE.runUrl,
      repository: { full_name: 'Nancyingcode/ok-wuthering-waves' },
      head_repository: { full_name: 'Nancyingcode/ok-wuthering-waves' },
    },
    comparison: 'identical',
    artifacts: [{ id: 123, name: 'release-assets', expired: false, workflow_run: { head_sha: SHA } }],
    existingRelease: null,
    taggedSha: null,
    failUpload: false,
    ...overrides,
  };
  const writes = [];
  const uploadData = new Map();
  const github = {
    rest: {
      actions: {
        getWorkflowRun: async () => ({ data: state.run }),
        listWorkflowRunArtifacts: async () => {},
      },
      repos: {
        compareCommits: async () => ({ data: { status: state.comparison } }),
        getReleaseByTag: async () => state.existingRelease ? { data: state.existingRelease } : notFound(),
        getCommit: async () => state.taggedSha ? { data: { sha: state.taggedSha } } : notFound(),
        createRelease: async params => {
          writes.push(['draft', params]);
          return { data: { id: 456 } };
        },
        uploadReleaseAsset: async params => {
          writes.push(['upload', params.name]);
          if (state.failUpload) throw new Error('Upload failed');
          uploadData.set(params.name, params.data);
        },
        updateRelease: async params => {
          writes.push(['publish', params]);
          return { data: { html_url: 'https://github.com/example/release' } };
        },
      },
      git: {
        createTag: async params => {
          writes.push(['tag', params]);
          return { data: { sha: OTHER_SHA } };
        },
        createRef: async params => { writes.push(['ref', params]); },
      },
    },
    paginate: async () => state.artifacts,
  };
  return { github, state, writes, uploadData };
}

async function assetsFixture(t) {
  const tempRoot = path.resolve(os.tmpdir());
  const directory = await fs.mkdtemp(path.join(tempRoot, 'publish-existing-build-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(directory)), tempRoot);
    assert.ok(path.basename(directory).startsWith('publish-existing-build-'));
    return fs.rm(directory, { recursive: true, force: true });
  });
  await fs.writeFile(path.join(directory, 'ok-ww-personal-win32-setup.exe'), 'abc');
  await fs.writeFile(path.join(directory, 'ok-ww-personal-win32.zip'), Buffer.from([0, 255, 13, 10]));
  return directory;
}

function validate(fixture, inputs = {}) {
  return validateSource({ github: fixture.github, context: CONTEXT, version: SOURCE.version, runId: SOURCE.runId, ...inputs });
}

test('a completed same-repository build selects its original commit and artifact', async () => {
  const fixture = apiFixture({ comparison: 'ahead' });
  assert.deepEqual(await validate(fixture), SOURCE);
  assert.deepEqual(fixture.writes, []);
});

test('prerelease versions are identified without shell evaluation', async () => {
  const result = await validate(apiFixture(), { version: 'v1.0.3-beta.1' });
  assert.equal(result.prerelease, true);
});

for (const version of ['personal', 'v01.0.2', 'v1.0', 'v1.0.2; echo test', '../v1.0.2']) {
  test(`invalid version is rejected: ${version}`, async () => {
    await assert.rejects(validate(apiFixture(), { version }), /Version must/);
  });
}

for (const runId of ['-1', '12abc', '1.5', '9007199254740992']) {
  test(`invalid run ID is rejected: ${runId}`, async () => {
    await assert.rejects(validate(apiFixture(), { runId }), /Run ID/);
  });
}

for (const [name, modify, error] of [
  ['failed build', f => { f.state.run.conclusion = 'failure'; }, /completed successfully/],
  ['unfinished build', f => { f.state.run.status = 'in_progress'; }, /completed successfully/],
  ['different workflow', f => { f.state.run.path = '.github/workflows/test.yml'; }, /Windows Release/],
  ['fork build', f => { f.state.run.head_repository.full_name = 'someone/fork'; }, /fork or pull request/],
  ['pull request build', f => { f.state.run.event = 'pull_request'; }, /fork or pull request/],
  ['invalid commit SHA', f => { f.state.run.head_sha = 'personal'; }, /invalid commit SHA/],
  ['unmerged commit', f => { f.state.comparison = 'behind'; }, /merged into personal/],
  ['diverged commit', f => { f.state.comparison = 'diverged'; }, /merged into personal/],
  ['expired artifact', f => { f.state.artifacts[0].expired = true; }, /unexpired/],
  ['missing artifact', f => { f.state.artifacts = []; }, /unexpired/],
  ['ambiguous artifacts', f => { f.state.artifacts.push({ ...f.state.artifacts[0], id: 124 }); }, /exactly one/],
  ['artifact from another commit', f => { f.state.artifacts[0].workflow_run.head_sha = OTHER_SHA; }, /Artifact commit/],
  ['existing release', f => { f.state.existingRelease = { id: 789 }; }, /already exists/],
  ['conflicting tag', f => { f.state.taggedSha = OTHER_SHA; }, /not source build commit/],
]) {
  test(`${name} cannot be published`, async () => {
    const fixture = apiFixture();
    modify(fixture);
    await assert.rejects(validate(fixture), error);
    assert.deepEqual(fixture.writes, []);
  });
}

test('API permission errors are not mistaken for a missing release', async () => {
  const fixture = apiFixture();
  fixture.github.rest.repos.getReleaseByTag = async () => { throw Object.assign(new Error('Forbidden'), { status: 403 }); };
  await assert.rejects(validate(fixture), /Forbidden/);
});

test('publishing uploads original bytes and matching SHA256 before making a release public', async t => {
  const directory = await assetsFixture(t);
  const fixture = apiFixture();
  await publishRelease({ github: fixture.github, context: CONTEXT, source: SOURCE, directory });
  assert.deepEqual(fixture.writes.map(([operation]) => operation), ['tag', 'ref', 'draft', 'upload', 'upload', 'upload', 'publish']);
  assert.equal(fixture.writes[0][1].object, SHA);
  assert.equal(fixture.writes[1][1].sha, OTHER_SHA);
  const draft = fixture.writes[2][1];
  assert.equal(draft.target_commitish, SHA);
  assert.equal(draft.draft, true);
  assert.equal(draft.prerelease, false);
  assert.match(draft.body, /41366113840/);

  const manifest = fixture.uploadData.get('SHA256SUMS.txt').toString('utf8');
  assert.match(manifest, /^ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  ok-ww-personal-win32-setup\.exe\n/);
  assert.equal(manifest.trimEnd().split('\n').length, 2);
  for (const [name, data] of fixture.uploadData) {
    assert.deepEqual(data, await fs.readFile(path.join(directory, name)));
    if (name !== 'SHA256SUMS.txt') {
      const digest = createHash('sha256').update(data).digest('hex');
      assert.ok(manifest.includes(`${digest}  ${name}\n`));
    }
  }
});

test('a matching existing tag is reused without moving it', async t => {
  const fixture = apiFixture({ taggedSha: SHA });
  await publishRelease({ github: fixture.github, context: CONTEXT, source: SOURCE, directory: await assetsFixture(t) });
  assert.equal(fixture.writes.some(([operation]) => ['tag', 'ref'].includes(operation)), false);
});

test('prerelease publication cannot become the latest stable release', async t => {
  const fixture = apiFixture();
  const source = { ...SOURCE, version: 'v1.0.3-beta.1', prerelease: true };
  await publishRelease({ github: fixture.github, context: CONTEXT, source, directory: await assetsFixture(t) });
  assert.equal(fixture.writes.find(([operation]) => operation === 'draft')[1].prerelease, true);
  assert.equal(fixture.writes.find(([operation]) => operation === 'publish')[1].make_latest, 'false');
});

test('a release created after preflight is not overwritten', async t => {
  const fixture = apiFixture();
  const source = await validate(fixture);
  fixture.state.existingRelease = { id: 789 };
  await assert.rejects(publishRelease({ github: fixture.github, context: CONTEXT, source, directory: await assetsFixture(t) }), /already exists/);
  assert.deepEqual(fixture.writes, []);
});

test('a tag moved after preflight is not overwritten', async t => {
  const fixture = apiFixture();
  const source = await validate(fixture);
  fixture.state.taggedSha = OTHER_SHA;
  await assert.rejects(publishRelease({ github: fixture.github, context: CONTEXT, source, directory: await assetsFixture(t) }), /not source build commit/);
  assert.deepEqual(fixture.writes, []);
});

test('a supplied checksum is replaced with a digest of the actual uploaded bytes', async t => {
  const fixture = apiFixture();
  const directory = await assetsFixture(t);
  await fs.writeFile(path.join(directory, 'SHA256SUMS.txt'), 'incorrect supplied checksum');
  await publishRelease({ github: fixture.github, context: CONTEXT, source: SOURCE, directory });
  const manifest = fixture.uploadData.get('SHA256SUMS.txt').toString('utf8');
  assert.equal(manifest.includes('incorrect supplied checksum'), false);
  assert.match(manifest, /^ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad /);
});

test('failed uploads keep the release unpublished', async t => {
  const fixture = apiFixture({ failUpload: true });
  await assert.rejects(publishRelease({ github: fixture.github, context: CONTEXT, source: SOURCE, directory: await assetsFixture(t) }), /Upload failed/);
  assert.equal(fixture.writes.some(([operation]) => operation === 'publish'), false);
});

for (const kind of ['missing', 'empty', 'unexpected']) {
  test(`${kind} assets are rejected before creating a tag or release`, async t => {
    const directory = await assetsFixture(t);
    const installer = path.join(directory, 'ok-ww-personal-win32-setup.exe');
    if (kind === 'missing') await fs.unlink(installer);
    if (kind === 'empty') await fs.writeFile(installer, '');
    if (kind === 'unexpected') await fs.writeFile(path.join(directory, 'unexpected.exe'), 'extra');
    const fixture = apiFixture();
    await assert.rejects(publishRelease({ github: fixture.github, context: CONTEXT, source: SOURCE, directory }));
    assert.deepEqual(fixture.writes, []);
  });
}
