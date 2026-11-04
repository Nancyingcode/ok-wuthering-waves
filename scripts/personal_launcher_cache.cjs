const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');

const SCHEMA = 2; // Bump to invalidate immutable caches or require refreshed binaries.
const SETTINGS = {
  name: 'ok-ww-personal', version: 'v1.2.3',
  commit: '52bc6d986aae93615673960256549f51f13c92b6',
  profile: 'release', target: 'x86_64-pc-windows-msvc', features: 'default',
};
const INPUT_FILES = [
  '.github/workflows/build.yml', 'launcher/pyappify-profiles.patch',
  'launcher/pyappify.personal.yml', 'icons/icon.ico', 'icons/icon.png',
  'scripts/build_personal_launcher.ps1', 'scripts/personal_launcher_common.ps1',
  'scripts/personal_launcher_cache.cjs',
  'build/launcher-metadata/tauri-build.json',
];
const RELEASE_FILES = ['ok-ww-personal-win32-setup.exe', 'ok-ww-personal-win32.zip'];
const metadataPath = (root, name) => path.join(root, 'build/launcher-metadata', `${name}.json`);
const hash = value => createHash('sha256').update(value).digest('hex');
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}
const serialize = value => JSON.stringify(canonical(value));
const readJson = filename => JSON.parse(fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, ''));
function writeIfChanged(filename, content) {
  const bytes = Buffer.from(content);
  if (fs.existsSync(filename) && fs.readFileSync(filename).equals(bytes)) return;
  fs.mkdirSync(path.dirname(filename), { recursive: true });
  fs.writeFileSync(filename, bytes);
}
const writeJson = (filename, value) => writeIfChanged(filename, `${JSON.stringify(canonical(value), null, 2)}\n`);
function fileHashes(root, files) {
  return Object.fromEntries([...files].map(file => file.replaceAll('\\', '/')).sort().map(file => {
    if (file.startsWith('/') || file.split('/').includes('..') || path.isAbsolute(file)) throw new Error(`Unsafe input: ${file}`);
    const filename = path.join(root, file);
    if (!fs.lstatSync(filename).isFile()) throw new Error(`Input must be a regular file: ${file}`);
    return [file, hash(fs.readFileSync(filename))];
  }));
}
const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], { encoding: 'utf8' }).trim();
function preparation(root) {
  const source = path.join(root, 'build/pyappify');
  if (git(source, 'rev-parse', 'HEAD') !== SETTINGS.commit) throw new Error('Unexpected PyAppify commit');
  const files = execFileSync('git', ['-C', source, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], { encoding: 'utf8' })
    .split('\0').filter(Boolean);
  return { schema: SCHEMA, settings: SETTINGS, files: fileHashes(root, INPUT_FILES), sourceFiles: fileHashes(source, new Set(files)) };
}
function assertPrepared(root) {
  const expected = readJson(metadataPath(root, 'prepared'));
  const current = preparation(root);
  if (serialize(expected) !== serialize(current)) throw new Error('Launcher inputs changed; run -Phase Prepare again.');
  return hash(serialize(current));
}
function receipt(root, phase, write = false) {
  const prepared = assertPrepared(root);
  const filename = metadataPath(root, phase);
  const record = write ? null : readJson(filename);
  const buildFingerprint = buildIdentity(currentFingerprint(root));
  if (write) writeJson(filename, { prepared, buildFingerprint, passed: true });
  else {
    const matchesCurrentBuild = record.passed === true && record.prepared === prepared && record.buildFingerprint === buildFingerprint;
    if (!matchesCurrentBuild) {
      throw new Error(`Run -Phase ${phase} successfully with the current inputs and toolchain before continuing.`);
    }
  }
}
function fingerprint(inputs) {
  // A tested binary remains reusable after a runner refresh. Tool selectors in
  // build.yml are hashed with the recipe; observed versions are provenance.
  const { tools, ...artifactInputs } = inputs;
  const digest = hash(serialize({ schema: SCHEMA, inputs: artifactInputs }));
  return { schema: SCHEMA, fingerprint: digest, key: `personal-launcher-win-x64-v${SCHEMA}-${digest}`,
    inputs: artifactInputs, tools };
}
const buildIdentity = data => hash(serialize({ fingerprint: data.fingerprint, tools: data.tools }));
function toolVersions() {
  const version = (command, args) => execFileSync(command, args, { encoding: 'utf8' }).trim();
  // Invoke pnpm through PowerShell: pnpm.cmd is not an executable for execFile on Windows.
  const vswhere = path.join(process.env['ProgramFiles(x86)'] || 'C:/Program Files (x86)', 'Microsoft Visual Studio/Installer/vswhere.exe');
  return {
    node: process.version,
    pnpm: version('pwsh', ['-NoProfile', '-Command', '& pnpm.cmd --version; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }']),
    rustc: version('rustc', ['-Vv']), cargo: version('cargo', ['-V']),
    powershell: version('pwsh', ['--version']),
    msvcInstallation: fs.existsSync(vswhere) ? version(vswhere, ['-latest', '-products', '*', '-prerelease', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationVersion']) : 'unavailable',
    os: `${process.platform}/${process.arch}/${os.release()}`,
    imageOS: process.env.ImageOS || 'local', imageVersion: process.env.ImageVersion || 'local',
  };
}
function buildEnvironment(env = process.env) {
  // Include compiler/bundler overrides without storing credential values in artifacts.
  return Object.fromEntries(Object.keys(env).filter(key =>
    /^(CARGO|RUST|TAURI|VITE|PNPM|NPM_CONFIG|NODE_|CMAKE|CC(?:_|$)|CXX(?:_|$)|AR(?:_|$)|CFLAGS|CXXFLAGS|LDFLAGS|CL$|_CL_$|LINK$|_LINK_$|VC|WindowsSDK|SOURCE_DATE_EPOCH$|CI$)/i.test(key)
  ).sort().map(key => [key, hash(env[key])]));
}
function currentFingerprint(root, tools = toolVersions()) {
  return fingerprint({ prepared: preparation(root), tools, environment: buildEnvironment(),
    platform: { os: process.platform, architecture: process.arch, target: SETTINGS.target } });
}
function readFingerprint(root) {
  const data = readJson(metadataPath(root, 'fingerprint'));
  const current = currentFingerprint(root);
  if (serialize(data) !== serialize(current)) throw new Error('Build environment or inputs changed after fingerprinting.');
  return data;
}
function artifactRecords(directory) {
  return RELEASE_FILES.map(name => {
    const filename = path.join(directory, name);
    const stat = fs.lstatSync(filename);
    if (!stat.isFile() || stat.size === 0) throw new Error(`Missing or empty artifact: ${name}`);
    return { name, bytes: stat.size, sha256: hash(fs.readFileSync(filename)) };
  });
}
function validateCache(directory, expected, releaseManifest) {
  try {
    const manifest = releaseManifest || readJson(path.join(directory, 'manifest.json'));
    if (manifest.schema !== SCHEMA || manifest.fingerprint !== expected.fingerprint) throw new Error('Manifest schema/fingerprint mismatch');
    if (manifest.upstreamCommit !== SETTINGS.commit) throw new Error('Manifest upstream commit mismatch');
    if (serialize(manifest.inputs) !== serialize(expected.inputs)) throw new Error('Manifest inputs mismatch');
    const tools = manifest.buildTools;
    const hasBuildTools = tools && ['node', 'pnpm', 'rustc', 'cargo', 'powershell', 'msvcInstallation', 'os', 'imageOS', 'imageVersion']
      .every(key => typeof tools[key] === 'string' && tools[key].length > 0);
    if (!hasBuildTools) throw new Error('Missing launcher build tool provenance');
    const test = manifest.launcherTest;
    const hasMatchingTest = test?.passed === true && test.name === 'launcher_profiles' &&
      test.fingerprint === expected.fingerprint &&
      test.buildFingerprint === buildIdentity({ fingerprint: expected.fingerprint, tools });
    if (!hasMatchingTest) throw new Error('Missing matching launcher test receipt');
    if (!manifest.source?.sha || !manifest.source.repository) throw new Error('Missing launcher build provenance');
    if (serialize(manifest.artifacts) !== serialize(artifactRecords(directory))) throw new Error('Artifact size/hash mismatch');
    const extra = fs.readdirSync(directory).filter(name => ![...RELEASE_FILES, releaseManifest ? 'SHA256SUMS.txt' : 'manifest.json'].includes(name));
    if (extra.length) throw new Error(`Unexpected cache files: ${extra.join(', ')}`);
    return { valid: true, manifest };
  } catch (error) {
    return { valid: false, reason: error.message };
  }
}
function clearCache(root) {
  const directory = path.resolve(root, 'build/launcher-cache');
  if (path.dirname(directory) !== path.resolve(root, 'build') || path.basename(directory) !== 'launcher-cache') throw new Error('Unsafe cache path');
  const build = path.resolve(root, 'build');
  if (fs.existsSync(build) && fs.realpathSync(build).toLowerCase() !== build.toLowerCase()) throw new Error('Refusing to clear a cache through a linked build directory');
  if (fs.existsSync(directory) && fs.lstatSync(directory).isSymbolicLink()) throw new Error('Refusing to clear a linked cache directory');
  fs.rmSync(directory, { recursive: true, force: true });
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}
function createManifest(root, data) {
  receipt(root, 'Build');
  receipt(root, 'Test');
  receipt(root, 'Bundle');
  const directory = clearCache(root);
  for (const name of RELEASE_FILES) fs.copyFileSync(path.join(root, 'dist', name), path.join(directory, name));
  const repository = process.env.GITHUB_REPOSITORY || git(root, 'remote', 'get-url', 'origin');
  const runId = process.env.GITHUB_RUN_ID || null;
  const manifest = {
    schema: SCHEMA, fingerprint: data.fingerprint, inputs: data.inputs, buildTools: data.tools,
    upstreamCommit: SETTINGS.commit,
    source: { repository, sha: process.env.GITHUB_SHA || git(root, 'rev-parse', 'HEAD'), runId,
      runUrl: runId ? `${process.env.GITHUB_SERVER_URL || 'https://github.com'}/${repository}/actions/runs/${runId}` : null },
    launcherTest: { name: 'launcher_profiles', passed: true, fingerprint: data.fingerprint, buildFingerprint: buildIdentity(data) },
    artifacts: artifactRecords(directory),
  };
  writeJson(path.join(directory, 'manifest.json'), manifest);
  writeJson(metadataPath(root, 'manifest'), manifest);
  return manifest;
}
function output(name, value) {
  if (process.env.GITHUB_OUTPUT) fs.appendFileSync(process.env.GITHUB_OUTPUT, `${name}=${value}\n`);
  console.log(`${name}=${value}`);
}
function prepareLock(root) {
  const filename = path.join(root, 'build/pyappify/src-tauri/Cargo.lock');
  const text = fs.readFileSync(filename, 'utf8').replace('name = "pyappify"', `name = "${SETTINGS.name}"`);
  const [header, ...packages] = text.split(/(?=^\[\[package\]\])/m);
  // Cargo sorts packages by name. Rename the root lock entry before caching so
  // the first build cannot invalidate the prepared snapshot or dependency key.
  packages.sort((a, b) => {
    const name = part => part.match(/^name = "([^"]+)"/m)[1];
    return name(a) < name(b) ? -1 : name(a) > name(b) ? 1 : 0;
  });
  writeIfChanged(filename, header + packages.join(''));
}
function summary(root) {
  const readOptional = name => fs.existsSync(metadataPath(root, name)) ? readJson(metadataPath(root, name)) : null;
  const data = readOptional('fingerprint');
  const cache = readOptional('cache-result');
  const manifestFile = path.join(root, 'build/launcher-cache/manifest.json');
  const manifest = fs.existsSync(manifestFile) ? readJson(manifestFile) : null;
  const isTagPush = process.env.GITHUB_EVENT_NAME === 'push' && process.env.GITHUB_REF?.startsWith('refs/tags/v');
  const report = {
    currentSha: process.env.GITHUB_SHA || git(root, 'rev-parse', 'HEAD'),
    runUrl: process.env.GITHUB_RUN_ID ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}` : null,
    fingerprint: data?.fingerprint, tools: data?.tools, cache,
    rustCacheHit: process.env.RUST_CACHE_HIT || 'not restored', pnpmCacheHit: process.env.PNPM_CACHE_HIT || 'not restored',
    launcherSource: manifest?.source, launcherBuildTools: manifest?.buildTools, artifacts: manifest?.artifacts,
    stages: readOptional('timings') || [], workflowSteps: JSON.parse(process.env.WORKFLOW_STEPS || '{}'),
    automaticPublish: isTagPush ? 'gated by validate/synchronize/package_personal' : false,
  };
  writeJson(metadataPath(root, 'report'), report);
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `## Personal launcher build\n\nCurrent business SHA: \`${report.currentSha}\`\n\nPath: **${cache?.reuse ? 'complete artifact hit' : 'full build'}**; Rust cache: ${report.rustCacheHit}; pnpm cache: ${report.pnpmCacheHit}.\n\nLauncher source: ${manifest?.source.runUrl || manifest?.source.sha || 'not built'}\n\nStructured timings, cache diagnostics, tool versions and file hashes: launcher-build-metadata artifact. Action/post-action durations and job/wall-clock times are available in the linked run's Jobs view.\n\n\`\`\`json\n${JSON.stringify(report, null, 2)}\n\`\`\`\n`);
  }
}
function main(command, root = path.resolve(__dirname, '..'), argument) {
  switch (command) {
    case 'settings': console.log(JSON.stringify(SETTINGS)); break;
    case 'prepare-lock': prepareLock(root); break;
    case 'prepare': writeJson(metadataPath(root, 'prepared'), preparation(root)); break;
    case 'assert-prepared': assertPrepared(root); break;
    case 'receipt': receipt(root, argument, true); break;
    case 'require': receipt(root, argument); break;
    case 'fingerprint': {
      assertPrepared(root);
      const tools = toolVersions();
      if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('Expected Windows x64 launcher builder');
      if (!tools.rustc.includes(`host: ${SETTINGS.target}`)) throw new Error(`Expected Rust host ${SETTINGS.target}`);
      for (const key of ['CARGO_TARGET_DIR', 'CARGO_BUILD_TARGET', 'TAURI_CONFIG']) {
        if (process.env[key]) throw new Error(`Unsupported output/config override: ${key}`);
      }
      const data = currentFingerprint(root, tools);
      writeJson(metadataPath(root, 'fingerprint'), data);
      output('key', data.key);
      break;
    }
    case 'verify': {
      const expected = readFingerprint(root);
      const forced = process.env.FORCE_LAUNCHER_REBUILD === 'true';
      const exactHit = process.env.LAUNCHER_CACHE_HIT === 'true';
      const result = forced || !exactHit ? { valid: false, reason: forced ? 'Forced rebuild' : 'No exact cache hit' }
        : validateCache(path.join(root, 'build/launcher-cache'), expected);
      writeJson(metadataPath(root, 'cache-result'), { reuse: result.valid, reason: result.reason || 'Manifest and both artifacts verified', exactHit, forced });
      if (result.valid) writeJson(metadataPath(root, 'manifest'), result.manifest);
      if (!result.valid) {
        console.log(`Rebuilding launcher: ${result.reason}`);
        if (exactHit && !forced) console.log('::warning::Invalid immutable launcher cache; rebuild locally and bump SCHEMA to replace the remote key.');
        clearCache(root);
        fs.rmSync(metadataPath(root, 'manifest'), { force: true });
      }
      output('reuse_launcher', result.valid);
      break;
    }
    case 'manifest': createManifest(root, readFingerprint(root)); break;
    case 'stage': {
      const directory = path.join(root, 'build/launcher-cache');
      const result = validateCache(directory, readFingerprint(root));
      if (!result.valid) throw new Error(`Cannot stage launcher: ${result.reason}`);
      const destination = path.join(root, 'pyappify_dist');
      fs.mkdirSync(destination, { recursive: true });
      const unexpected = fs.readdirSync(destination).filter(name => !RELEASE_FILES.includes(name));
      if (unexpected.length) throw new Error(`Unexpected release assets: ${unexpected.join(', ')}`);
      for (const name of RELEASE_FILES) fs.copyFileSync(path.join(directory, name), path.join(destination, name));
      break;
    }
    case 'summary': summary(root); break;
    default: throw new Error(`Unknown command: ${command}`);
  }
}
module.exports = { SCHEMA, SETTINGS, INPUT_FILES, RELEASE_FILES, fingerprint, fileHashes, validateCache,
  writeIfChanged, preparation, assertPrepared, receipt, createManifest, clearCache, prepareLock, buildEnvironment, buildIdentity, serialize, main };
if (require.main === module) {
  try { main(process.argv[2], process.argv[3] && path.resolve(process.argv[3]), process.argv[4]); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
