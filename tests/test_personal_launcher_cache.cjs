const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync, execFileSync } = require('node:child_process');
const { test } = require('node:test');
const { SCHEMA, SETTINGS, INPUT_FILES, RELEASE_FILES, fingerprint, fileHashes, validateCache,
  writeIfChanged, assertPrepared, receipt, createManifest, buildEnvironment, buildIdentity, main } = require('../scripts/personal_launcher_cache.cjs');

const ROOT = path.resolve(__dirname, '..');
const OLD_TOOLS = {
  node: 'v24.22.1', pnpm: '11.28.10', rustc: 'rustc 1.99.1', cargo: 'cargo 1.99.1',
  powershell: 'PowerShell 7.6.7', msvcInstallation: '18.11.12315.225',
  os: 'win32/x64/10.0.26100', imageOS: 'win25-vs2026', imageVersion: '20261021.278.1',
};
const NEW_TOOLS = { ...OLD_TOOLS, node: 'v24.22.2', powershell: 'PowerShell 7.6.9',
  msvcInstallation: '18.11.12328.437', imageVersion: '20261030.294.1' };
function fixture(t) {
  const tempRoot = fs.realpathSync(os.tmpdir());
  const directory = fs.mkdtempSync(path.join(tempRoot, 'personal-launcher-test-'));
  t.after(() => {
    assert.equal(path.dirname(directory), tempRoot);
    assert.ok(path.basename(directory).startsWith('personal-launcher-test-'));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return directory;
}
function write(root, name, value) {
  const filename = path.join(root, name);
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.writeFileSync(filename, value);
}
function inputsFixture(t) {
  const root = fixture(t);
  for (const name of INPUT_FILES) write(root, name, `input: ${name}`);
  return root;
}
function inputs(root, tools = OLD_TOOLS) {
  return { files: fileHashes(root, INPUT_FILES), tools, settings: { ...SETTINGS },
    platform: { os: 'win32', architecture: 'x64', target: SETTINGS.target } };
}
function cacheFixture(t) {
  const directory = fixture(t);
  const expected = fingerprint({ files: { 'launcher.patch': 'a'.repeat(64) }, tools: OLD_TOOLS });
  const manifest = {
    schema: SCHEMA, fingerprint: expected.fingerprint, inputs: structuredClone(expected.inputs), upstreamCommit: SETTINGS.commit,
    buildTools: structuredClone(OLD_TOOLS),
    source: { repository: 'Nancyingcode/ok-wuthering-waves', sha: 'a'.repeat(40), runId: 123, runUrl: 'https://github.com/example/actions/runs/123' },
    launcherTest: { name: 'launcher_profiles', passed: true, fingerprint: expected.fingerprint, buildFingerprint: buildIdentity(expected) },
    artifacts: RELEASE_FILES.map(name => {
      write(directory, name, `binary-${name}`);
      const { createHash } = require('node:crypto');
      const data = fs.readFileSync(path.join(directory, name));
      return { name, bytes: data.length, sha256: createHash('sha256').update(data).digest('hex') };
    }),
  };
  const save = () => write(directory, 'manifest.json', JSON.stringify(manifest));
  save();
  return { directory, expected, manifest, save };
}

test('business commits, tags and Python files do not participate in launcher input hashing', t => {
  const root = inputsFixture(t);
  const before = fingerprint(inputs(root));
  write(root, 'src/task/Business.py', 'changed business logic');
  write(root, 'requirements.txt', 'new Python requirement');
  // Provenance is stored separately from the fingerprint input object.
  const manifest = cacheFixture(t);
  manifest.manifest.source.sha = 'b'.repeat(40);
  manifest.save();
  assert.deepEqual(fingerprint(inputs(root)), before);
  assert.equal(validateCache(manifest.directory, manifest.expected).valid, true);
});

for (const filename of INPUT_FILES) {
  test(`changing ${filename} invalidates the fingerprint`, t => {
    const root = inputsFixture(t);
    const before = fingerprint(inputs(root)).fingerprint;
    fs.appendFileSync(path.join(root, filename), ' changed');
    assert.notEqual(fingerprint(inputs(root)).fingerprint, before);
  });
}
test('observed tool and runner refreshes keep the artifact key but change the build receipt identity', t => {
  const root = inputsFixture(t);
  const baseline = inputs(root);
  for (const key of Object.keys(baseline.tools)) {
    const changed = structuredClone(baseline);
    changed.tools[key] += '-changed';
    assert.equal(fingerprint(changed).key, fingerprint(baseline).key, key);
    assert.notEqual(buildIdentity(fingerprint(changed)), buildIdentity(fingerprint(baseline)), key);
  }
  const previous = fingerprint(inputs(root, OLD_TOOLS));
  const current = fingerprint(inputs(root, NEW_TOOLS));
  assert.equal(current.key, previous.key);
  assert.deepEqual(current.tools, NEW_TOOLS);
  assert.deepEqual(previous.tools, OLD_TOOLS);
  assert.equal(Object.hasOwn(current.inputs, 'tools'), false);
});
test('target, architecture, profile, features and upstream changes still invalidate the artifact key', t => {
  const baseline = inputs(inputsFixture(t));
  for (const [group, key, value] of [
    ['platform', 'os', 'linux'], ['platform', 'architecture', 'arm64'],
    ['platform', 'target', 'aarch64-pc-windows-msvc'], ['settings', 'profile', 'debug'],
    ['settings', 'features', 'other'], ['settings', 'commit', 'b'.repeat(40)],
  ]) {
    const changed = structuredClone(baseline);
    changed[group][key] = value;
    assert.notEqual(fingerprint(changed).key, fingerprint(baseline).key, `${group}.${key}`);
  }
});
test('compiler and bundler overrides remain fingerprint inputs and credentials are not recorded', () => {
  const baseline = { CARGO_INCREMENTAL: '0', RUSTFLAGS: '-C codegen-units=1', TAURI_SIGNING_PASSWORD: 'secret', GITHUB_TOKEN: 'secret' };
  const env = buildEnvironment(baseline);
  assert.equal(JSON.stringify(env).includes('secret'), false);
  assert.equal(Object.hasOwn(env, 'GITHUB_TOKEN'), false);
  for (const key of ['CARGO_INCREMENTAL', 'RUSTFLAGS', 'TAURI_SIGNING_PASSWORD', 'SOURCE_DATE_EPOCH', 'CL']) {
    const changed = buildEnvironment({ ...baseline, [key]: 'changed' });
    assert.notEqual(fingerprint({ environment: changed }).key, fingerprint({ environment: env }).key, key);
  }
});
test('enumeration/object order, path separators and mtimes do not change the fingerprint', t => {
  const root = inputsFixture(t);
  const files = fileHashes(root, INPUT_FILES);
  const reversed = fileHashes(root, [...INPUT_FILES].reverse().map(name => name.replaceAll('/', '\\')));
  fs.utimesSync(path.join(root, INPUT_FILES[0]), new Date(0), new Date(0));
  assert.deepEqual(fileHashes(root, INPUT_FILES), files);
  assert.equal(fingerprint({ files, tools: { rust: 'a', node: 'b' } }).fingerprint,
    fingerprint({ tools: { node: 'b', rust: 'a' }, files: reversed }).fingerprint);
});
test('missing required inputs fail rather than hashing a partial input set', t => {
  const root = inputsFixture(t);
  fs.unlinkSync(path.join(root, INPUT_FILES[0]));
  assert.throws(() => inputs(root), /ENOENT/);
});

test('a complete matching cache is reusable without requiring current business SHA', t => {
  const { directory, expected } = cacheFixture(t);
  assert.equal(validateCache(directory, expected).valid, true);
});
test('a cache built and tested on the previous runner is accepted with its original tool provenance', t => {
  const { directory, expected, manifest } = cacheFixture(t);
  const current = fingerprint({ ...expected.inputs, tools: NEW_TOOLS });
  assert.equal(current.key, expected.key);
  const result = validateCache(directory, current);
  assert.equal(result.valid, true);
  assert.deepEqual(result.manifest.buildTools, OLD_TOOLS);
  assert.notDeepEqual(result.manifest.buildTools, current.tools);
  assert.deepEqual(result.manifest.source, manifest.source);
});
for (const [name, change] of [
  ['schema mismatch', f => { f.manifest.schema++; f.save(); }],
  ['fingerprint mismatch', f => { f.manifest.fingerprint = 'b'.repeat(64); f.save(); }],
  ['input mismatch', f => { f.manifest.inputs.files['launcher.patch'] = 'other'; f.save(); }],
  ['missing tests', f => { delete f.manifest.launcherTest; f.save(); }],
  ['failed tests', f => { f.manifest.launcherTest.passed = false; f.save(); }],
  ['tests for other input', f => { f.manifest.launcherTest.fingerprint = 'c'.repeat(64); f.save(); }],
  ['tests for other build tools', f => { f.manifest.launcherTest.buildFingerprint = 'c'.repeat(64); f.save(); }],
  ['changed build provenance', f => { f.manifest.buildTools.node = 'other'; f.save(); }],
  ['missing build tool provenance', f => { delete f.manifest.buildTools; f.save(); }],
  ['incomplete build tool provenance', f => { delete f.manifest.buildTools.rustc; f.save(); }],
  ['missing provenance', f => { delete f.manifest.source; f.save(); }],
  ['missing installer', f => fs.unlinkSync(path.join(f.directory, RELEASE_FILES[0]))],
  ['empty ZIP', f => fs.writeFileSync(path.join(f.directory, RELEASE_FILES[1]), '')],
  ['same-size altered bytes', f => {
    const filename = path.join(f.directory, RELEASE_FILES[0]);
    const data = fs.readFileSync(filename); data[0] ^= 255; fs.writeFileSync(filename, data);
  }],
  ['bad byte count', f => { f.manifest.artifacts[0].bytes++; f.save(); }],
  ['duplicate artifact record', f => { f.manifest.artifacts[1] = f.manifest.artifacts[0]; f.save(); }],
  ['extra cache files', f => write(f.directory, 'timing.json', '{}')],
  ['invalid manifest JSON', f => write(f.directory, 'manifest.json', '{bad')],
]) {
  test(`${name} rejects cache reuse`, t => {
    const f = cacheFixture(t);
    change(f);
    const result = validateCache(f.directory, f.expected);
    assert.equal(result.valid, false);
    assert.ok(result.reason);
  });
}
test('identical writes preserve byte content and mtime', t => {
  const root = fixture(t);
  const filename = path.join(root, 'file');
  writeIfChanged(filename, 'same\n');
  fs.utimesSync(filename, new Date(0), new Date(0));
  writeIfChanged(filename, 'same\n');
  assert.equal(fs.statSync(filename).mtimeMs, 0);
  writeIfChanged(filename, 'different\n');
  assert.equal(fs.readFileSync(filename, 'utf8'), 'different\n');
  assert.notEqual(fs.statSync(filename).mtimeMs, 0);
});
test('external command failures survive timing and stop subsequent work', t => {
  const root = fixture(t);
  const common = path.join(ROOT, 'scripts/personal_launcher_common.ps1').replaceAll("'", "''");
  const timing = path.join(root, 'timings.json').replaceAll("'", "''");
  const result = spawnSync('pwsh', ['-NoProfile', '-Command',
    `$ErrorActionPreference = 'Stop'; . '${common}'; $TimingPath = '${timing}'; Invoke-Timed 'failure' { Invoke-Checked 'node' @('-e', 'process.exit(17)') }; Write-Host 'BUNDLE_MUST_NOT_RUN'`,
  ], { encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /exit code 17/);
  assert.doesNotMatch(result.stdout, /BUNDLE_MUST_NOT_RUN/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'timings.json')))[0].succeeded, false);
});
test('a broken timing file cannot hide the external command failure', t => {
  const root = fixture(t);
  write(root, 'timings.json', '{broken');
  const common = path.join(ROOT, 'scripts/personal_launcher_common.ps1').replaceAll("'", "''");
  const timing = path.join(root, 'timings.json').replaceAll("'", "''");
  const result = spawnSync('pwsh', ['-NoProfile', '-Command',
    `$ErrorActionPreference = 'Stop'; . '${common}'; $TimingPath = '${timing}'; Invoke-Timed 'failure' { Invoke-Checked 'node' @('-e', 'process.exit(17)') }`,
  ], { encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /exit code 17/);
  assert.match(result.stdout, /Cannot write launcher timing record/);
});
test('PrepareOnly rejects conflicting explicit phases before invoking tools', () => {
  const result = spawnSync('pwsh', ['-NoProfile', '-File', path.join(ROOT, 'scripts/build_personal_launcher.ps1'), '-PrepareOnly', '-Phase', 'All'], { encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /cannot be combined/);
});

const integrationSource = process.env.LAUNCHER_INTEGRATION_SOURCE;
test('real pinned checkout: repeated Prepare is idempotent, stale state fails, receipts gate cache creation', { skip: !integrationSource }, t => {
  const root = fixture(t);
  for (const name of INPUT_FILES) write(root, name, fs.readFileSync(path.join(ROOT, name)));
  const source = path.join(root, 'build/pyappify');
  fs.mkdirSync(source, { recursive: true });
  fs.cpSync(path.join(integrationSource, '.git'), path.join(source, '.git'), { recursive: true });
  const tracked = execFileSync('git', ['-C', integrationSource, 'ls-files', '-z'], { encoding: 'utf8' }).split('\0').filter(Boolean);
  for (const name of tracked) write(source, name, fs.readFileSync(path.join(integrationSource, name)));
  const cargoManifest = path.join(source, 'src-tauri/Cargo.toml');
  fs.writeFileSync(cargoManifest, fs.readFileSync(cargoManifest, 'utf8').replace(/\r?\n/g, '\r\n'));
  const prepare = () => execFileSync('pwsh', ['-NoProfile', '-File', path.join(root, 'scripts/build_personal_launcher.ps1'), '-Phase', 'Prepare'], { encoding: 'utf8' });
  prepare();
  const beforeTauri = assertPrepared(root);
  // Reproduce the pinned CLI's normalization before Cargo, including when the
  // checkout was CRLF. It must not invalidate the prepared input identity.
  writeIfChanged(cargoManifest, fs.readFileSync(cargoManifest, 'utf8').replace(/\r\n/g, '\n'));
  assert.equal(assertPrepared(root), beforeTauri);
  const watched = [...tracked.map(name => path.join(source, name)),
    ...INPUT_FILES.map(name => path.join(root, name)), path.join(root, 'build/launcher-metadata/prepared.json')];
  const before = watched.map(filename => [fs.statSync(filename).mtimeMs, fs.readFileSync(filename)]);
  prepare();
  watched.forEach((filename, i) => {
    assert.equal(fs.statSync(filename).mtimeMs, before[i][0], filename);
    assert.deepEqual(fs.readFileSync(filename), before[i][1], filename);
  });
  const prepared = assertPrepared(root);
  write(source, 'src-tauri/target/keep', 'restored dependency');
  assert.equal(assertPrepared(root), prepared);
  prepare();
  assert.equal(fs.readFileSync(path.join(source, 'src-tauri/target/keep'), 'utf8'), 'restored dependency');
  assert.throws(() => createManifest(root, fingerprint({})), /ENOENT/);
  for (const phase of ['Build', 'Test', 'Bundle']) receipt(root, phase, true);
  const oldCompilerFlags = process.env.CARGO_PROFILE_RELEASE_CODEGEN_UNITS;
  try {
    process.env.CARGO_PROFILE_RELEASE_CODEGEN_UNITS = '32';
    assert.throws(() => receipt(root, 'Test'), /current inputs and toolchain/);
  } finally {
    if (oldCompilerFlags === undefined) delete process.env.CARGO_PROFILE_RELEASE_CODEGEN_UNITS;
    else process.env.CARGO_PROFILE_RELEASE_CODEGEN_UNITS = oldCompilerFlags;
  }
  for (const name of RELEASE_FILES) write(root, `dist/${name}`, `output ${name}`);
  // Source metadata is fixture-local; creating a manifest performs no Git writes.
  const oldSha = process.env.GITHUB_SHA;
  const oldRepository = process.env.GITHUB_REPOSITORY;
  const oldHit = process.env.LAUNCHER_CACHE_HIT;
  const oldForce = process.env.FORCE_LAUNCHER_REBUILD;
  const oldImage = process.env.ImageVersion;
  try {
    process.env.GITHUB_SHA = 'b'.repeat(40);
    process.env.GITHUB_REPOSITORY = 'Nancyingcode/ok-wuthering-waves';
    main('fingerprint', root);
    const data = JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/fingerprint.json')));
    createManifest(root, data);
    assert.equal(validateCache(path.join(root, 'build/launcher-cache'), data).valid, true);
    // A runner refresh may reuse complete tested artifacts, while phases of a
    // new build must never mix tools from before and after that refresh.
    process.env.ImageVersion = `${oldImage || 'local'}-refreshed`;
    assert.throws(() => receipt(root, 'Test'), /current inputs and toolchain/);
    main('fingerprint', root);
    const refreshed = JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/fingerprint.json')));
    assert.equal(refreshed.key, data.key);
    assert.notDeepEqual(refreshed.tools, data.tools);
    process.env.GITHUB_SHA = 'c'.repeat(40);
    process.env.LAUNCHER_CACHE_HIT = 'true';
    process.env.FORCE_LAUNCHER_REBUILD = 'false';
    main('verify', root);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/cache-result.json'))).reuse, true);
    const restored = JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/manifest.json')));
    assert.deepEqual(restored.buildTools, data.tools);
    assert.equal(restored.launcherTest.buildFingerprint, buildIdentity(data));
    main('stage', root);
    assert.deepEqual(fs.readdirSync(path.join(root, 'pyappify_dist')).sort(), [...RELEASE_FILES].sort());
    for (const name of RELEASE_FILES) {
      assert.deepEqual(fs.readFileSync(path.join(root, 'pyappify_dist', name)), fs.readFileSync(path.join(root, 'dist', name)));
    }
    if (oldImage === undefined) delete process.env.ImageVersion; else process.env.ImageVersion = oldImage;
    main('fingerprint', root);
    fs.appendFileSync(path.join(root, 'build/launcher-cache', RELEASE_FILES[0]), 'corrupt');
    main('verify', root);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/cache-result.json'))).reuse, false);
    assert.deepEqual(fs.readdirSync(path.join(root, 'build/launcher-cache')), []);
    createManifest(root, data);
    process.env.FORCE_LAUNCHER_REBUILD = 'true';
    main('verify', root);
    const forced = JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/cache-result.json')));
    assert.equal(forced.reuse, false);
    assert.equal(forced.reason, 'Forced rebuild');
    createManifest(root, data);
    process.env.FORCE_LAUNCHER_REBUILD = 'false';
    process.env.LAUNCHER_CACHE_HIT = 'false';
    main('verify', root);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'build/launcher-metadata/cache-result.json'))).reason, 'No exact cache hit');
  } finally {
    if (oldSha === undefined) delete process.env.GITHUB_SHA; else process.env.GITHUB_SHA = oldSha;
    if (oldRepository === undefined) delete process.env.GITHUB_REPOSITORY; else process.env.GITHUB_REPOSITORY = oldRepository;
    if (oldHit === undefined) delete process.env.LAUNCHER_CACHE_HIT; else process.env.LAUNCHER_CACHE_HIT = oldHit;
    if (oldForce === undefined) delete process.env.FORCE_LAUNCHER_REBUILD; else process.env.FORCE_LAUNCHER_REBUILD = oldForce;
    if (oldImage === undefined) delete process.env.ImageVersion; else process.env.ImageVersion = oldImage;
  }
  fs.appendFileSync(path.join(root, 'launcher/pyappify.personal.yml'), '\n# changed\n');
  assert.throws(() => assertPrepared(root), /inputs changed/);
  assert.throws(() => receipt(root, 'Test'), /inputs changed/);
  assert.throws(() => main('manifest', root));
});
