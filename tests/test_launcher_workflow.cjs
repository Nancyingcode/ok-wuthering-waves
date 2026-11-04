const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const workflow = fs.readFileSync(path.join(__dirname, '../.github/workflows/build.yml'), 'utf8');
const promotion = fs.readFileSync(path.join(__dirname, '../.github/workflows/publish-existing-build.yml'), 'utf8');
test('both Personal publishing entry points share runtime serialization, source binding and recovery implementation', () => {
  for (const content of [workflow, promotion]) {
    assert.match(content, /group: personal-runtime-publish-\$\{\{ github.repository \}\}/);
    assert.match(content, /publishRelease.*require\('\.\/\.github\/scripts\/publish_existing_build.cjs'\)/);
    assert.match(content, /PERSONAL_RUNTIME_MIGRATION_APPROVAL:/);
    assert.match(content, /launcher-build-metadata|metadataArtifactId/);
  }
  assert.match(promotion, /BUSINESS_SHA:.*fromJSON\(steps.source.outputs.result\).sha/);
  assert.match(workflow, /\$\{context.ref\}\^\{commit\}/);
  assert.match(workflow, /name: Create GitHub release\r?\n        if: github.repository == 'ok-oldking\/ok-wuthering-waves'/);
});
function job(name) {
  const match = workflow.match(new RegExp(`^  ${name}:\\r?\\n([\\s\\S]*?)(?=^  [a-z_]+:|$(?![\\s\\S]))`, 'm'));
  assert.ok(match, `Job ${name} exists`);
  return match[1];
}
function condition(body) {
  const expression = body.match(/^    if: >-\r?\n([\s\S]*?)(?=^    \S)/m)?.[1]
    || body.match(/^    if: (.+)$/m)?.[1];
  assert.ok(expression, 'Job has an explicit condition');
  return expression.replace(/\$\{\{|\}\}/g, '').trim();
}
function evaluate(expression, context) {
  // These conditions use only booleans, property reads and these three Actions
  // functions. Evaluate the actual workflow expression against outcome tables.
  return vm.runInNewContext(expression, { ...context, always: () => true,
    cancelled: () => context.cancelled || false, startsWith: (s, prefix) => s.startsWith(prefix) });
}
const upstream = 'ok-oldking/ok-wuthering-waves';
const fork = 'Nancyingcode/ok-wuthering-waves';
function context(repository = fork) {
  return { github: { repository, event_name: 'push', ref: 'refs/tags/v1.0.3' },
    inputs: { validate_only: false },
    needs: { validate: { result: 'success' }, synchronize: { result: 'success' },
      package: { result: repository === upstream ? 'success' : 'skipped' },
      package_personal: { result: repository === upstream ? 'skipped' : 'success' } } };
}
for (const repository of [fork, upstream]) {
  test(`${repository}: the successful matching build can publish with the other path skipped`, () => {
    assert.equal(evaluate(condition(job('publish')), context(repository)), true);
  });
  for (const dependency of ['validate', 'synchronize', repository === upstream ? 'package' : 'package_personal']) {
    for (const result of ['failure', 'cancelled', 'skipped']) {
      test(`${repository}: ${dependency}=${result} prevents publishing`, () => {
        const c = context(repository);
        c.needs[dependency].result = result;
        assert.equal(evaluate(condition(job('publish')), c), false);
      });
    }
  }
}
test('manual dispatch, cancellation and ordinary branch pushes never publish', () => {
  for (const change of [c => { c.github.event_name = 'workflow_dispatch'; },
    c => { c.github.ref = 'refs/heads/personal'; }, c => { c.cancelled = true; }]) {
    const c = context(); change(c);
    assert.equal(evaluate(condition(job('publish')), c), false);
  }
});
test('fork/upstream packaging are mutually exclusive and personal build has no validation dependency', () => {
  assert.equal(/^    needs:/m.test(job('package_personal')), false);
  for (const repository of [fork, upstream]) {
    const c = context(repository);
    assert.equal(evaluate(condition(job('package')), c), repository === upstream);
    assert.equal(evaluate(condition(job('package_personal')), c), repository !== upstream);
  }
});

for (const repository of [fork, upstream]) {
  test(`${repository}: validation-only applies to manual runs and cannot bypass tag packaging`, () => {
    for (const event of ['workflow_dispatch', 'push']) {
      for (const validateOnly of [true, false]) {
        const c = context(repository);
        c.github.event_name = event;
        c.inputs.validate_only = validateOnly;
        const shouldPackage = event === 'push' || !validateOnly;
        assert.equal(evaluate(condition(job('package')), c), shouldPackage && repository === upstream);
        assert.equal(evaluate(condition(job('package_personal')), c), shouldPackage && repository !== upstream);
        if (event === 'workflow_dispatch') assert.equal(evaluate(condition(job('publish')), c), false);
      }
    }
  });
  test(`${repository}: validation-only input cannot publish failed validation on a tag`, () => {
    const c = context(repository);
    c.inputs.validate_only = true;
    c.needs.validate.result = 'failure';
    assert.equal(evaluate(condition(job('publish')), c), false);
  });
}

function stepCondition(name) {
  const body = job('package_personal');
  const blocks = body.split(/^      - name: /m).slice(1);
  const block = blocks.find(value => value.split(/\r?\n/)[0] === name);
  assert.ok(block, `Step ${name} exists`);
  const expression = block.match(/^        if: (.+)$/m)?.[1];
  assert.ok(expression);
  return expression.replaceAll('.cache-hit', "['cache-hit']");
}
test('a verified artifact hit skips every expensive dependency/build/test/bundle step', () => {
  for (const reuse of ['true', 'false']) {
    const c = { env: { BUILD_EXE: 'true' }, steps: { verify_cache: { outputs: { reuse_launcher: reuse } } } };
    for (const name of ['Restore pnpm dependencies', 'Restore Rust dependencies', 'Build frontend and launcher',
      'Test launcher profiles', 'Bundle installer and portable ZIP', 'Record tested launcher artifacts']) {
      assert.equal(evaluate(stepCondition(name), c), reuse !== 'true', name);
    }
    for (const name of ['Stage verified release assets', 'Upload release assets']) {
      assert.equal(evaluate(stepCondition(name), c), true, name);
    }
  }
});
test('forcing a rebuild bypasses artifact restore and existing exact keys are never overwritten', () => {
  for (const force of [true, false]) {
    assert.equal(evaluate(stepCondition('Restore complete launcher artifacts'), {
      env: { BUILD_EXE: 'true' }, inputs: { force_launcher_rebuild: force },
    }), !force);
  }
  for (const hit of ['true', 'false', '']) {
    const c = { env: { BUILD_EXE: 'true' }, steps: {
      verify_cache: { outputs: { reuse_launcher: 'false' } }, launcher_cache: { outputs: { 'cache-hit': hit } },
    } };
    assert.equal(evaluate(stepCondition('Save tested launcher artifacts'), c), hit !== 'true');
  }
});

test('timing report handles skipped jobs without steps and excludes parallel publishing', async () => {
  const body = job('report_personal');
  const script = body.match(/^          script: \|\r?\n([\s\S]*?)(?=^      - name:)/m)[1].replace(/^ {12}/gm, '');
  const date = seconds => new Date(Date.UTC(2026, 10, 2, 0, 0, seconds)).toISOString();
  const jobs = [
    { name: '1 · Validate', status: 'completed', conclusion: 'success', started_at: date(0), completed_at: date(80), steps: [] },
    { name: '2 · Synchronize repositories', status: 'completed', conclusion: 'success', started_at: date(80), completed_at: date(84), steps: [] },
    { name: '3 · Build and optionally sign', status: 'completed', conclusion: 'skipped', started_at: null, completed_at: null, steps: null },
    { name: '3 · Build personal launcher', status: 'completed', conclusion: 'success', started_at: date(0), completed_at: date(100),
      steps: [{ name: 'Post Restore Rust dependencies', conclusion: 'success', started_at: date(90), completed_at: date(100) }] },
    { name: '4 · Publish', status: 'completed', conclusion: 'success', started_at: date(100), completed_at: date(130), steps: [] },
  ];
  let saved;
  const summary = { addHeading() { return this; }, addLink() { return this; }, addRaw() { return this; }, addCodeBlock() { return this; }, async write() {} };
  await vm.runInNewContext(`(async () => { ${script} })()`, {
    require: name => name === 'node:fs' ? { writeFileSync: (_, data) => { saved = JSON.parse(data); } } : require(name),
    process: { env: { GITHUB_RUN_ATTEMPT: '1', RUNNER_TEMP: '/fixture' } },
    github: { paginate: async () => jobs, rest: { actions: { listJobsForWorkflowRunAttempt() {} } } },
    context: { repo: { owner: 'Nancyingcode', repo: 'ok-wuthering-waves' }, sha: 'a'.repeat(40), serverUrl: 'https://github.com', runId: 123 },
    core: { setOutput() {}, summary },
  });
  assert.equal(saved.buildValidationWallSeconds, 100);
  assert.equal(saved.jobs.length, 4);
  assert.equal(saved.jobs[2].seconds, null);
  assert.deepEqual(saved.jobs[2].steps, []);
  assert.equal(saved.jobs[3].steps[0].seconds, 10);
});
