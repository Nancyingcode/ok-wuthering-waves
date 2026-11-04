const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { exportRuntime, hash, json, validateVersion, isPrerelease } = require('../../scripts/export_personal_runtime.cjs');
const { publishRuntime, loadStage } = require('./publish_personal_runtime.cjs');
const { validateLauncher, confirmMigration, validateConfiguration } = require('./personal_release_contract.cjs');
const { fileHashes } = require('../../scripts/personal_launcher_cache.cjs');

const BUILD_WORKFLOW = '.github/workflows/build.yml';
const ARTIFACT_NAME = 'release-assets';
const RELEASE_FILES = ['ok-ww-personal-win32-setup.exe', 'ok-ww-personal-win32.zip'];
const CHECKSUM_FILE = 'SHA256SUMS.txt';

async function getOrNull(request) {
  try {
    return (await request()).data;
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
}

async function checkVersion({ github, context, version, sha }) {
  const existingRelease = await getOrNull(() => github.rest.repos.getReleaseByTag({
    ...context.repo, tag: version,
  }));
  // Release identity includes artifact/runtime hashes and is checked after downloading
  // the selected build. A matching draft must remain resumable after a partial failure.
  if (existingRelease && !existingRelease.body?.includes('<!-- personal-runtime:')) throw new Error(`Release ${version} already exists without matching runtime provenance.`);
  // A missing tag returns 422 from the commits API; only the ref API's 404
  // identifies a new version without hiding other validation failures.
  const tagRef = await getOrNull(() => github.rest.git.getRef({
    ...context.repo, ref: `tags/${version}`,
  }));
  if (!tagRef) return false;

  const { data: taggedCommit } = await github.rest.repos.getCommit({
    ...context.repo, ref: `refs/tags/${version}`,
  });
  if (taggedCommit.sha !== sha) {
    throw new Error(`Tag ${version} points to ${taggedCommit.sha}, not source build commit ${sha}.`);
  }
  return true;
}

async function validateSource({ github, context, version, runId }) {
  version = version.trim();
  runId = String(runId).trim();
  validateVersion(version);
  const isValidRunId = /^[1-9]\d*$/.test(runId) && Number.isSafeInteger(Number(runId));
  if (!isValidRunId) throw new Error('Run ID must be a positive integer.');

  const { data: run } = await github.rest.actions.getWorkflowRun({
    ...context.repo, run_id: Number(runId),
  });
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  const isSameRepository = [run.repository?.full_name, run.head_repository?.full_name]
    .every(name => name?.toLowerCase() === repository.toLowerCase());
  const isSuccessfulBuild = run.status === 'completed' && run.conclusion === 'success';
  const isSupportedEvent = ['push', 'workflow_dispatch'].includes(run.event);
  if (!isSameRepository || run.path !== BUILD_WORKFLOW || !isSupportedEvent) {
    throw new Error('Source must be a Windows Release build from this repository, not a fork or pull request.');
  }
  if (!isSuccessfulBuild) throw new Error('Source build has not completed successfully.');
  if (!/^[0-9a-f]{40}$/i.test(run.head_sha)) throw new Error('Source build has an invalid commit SHA.');

  const { data: comparison } = await github.rest.repos.compareCommits({
    ...context.repo, base: run.head_sha, head: 'personal',
  });
  if (!['ahead', 'identical'].includes(comparison.status)) {
    throw new Error('Source build commit must already be merged into personal.');
  }

  const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts, {
    ...context.repo, run_id: Number(runId), per_page: 100,
  });
  const releaseArtifacts = artifacts.filter(artifact => artifact.name === ARTIFACT_NAME && !artifact.expired);
  if (releaseArtifacts.length !== 1) {
    throw new Error('Source build must have exactly one unexpired release-assets artifact.');
  }
  const artifact = releaseArtifacts[0];
  const hasMatchingArtifactCommit = !artifact.workflow_run?.head_sha || artifact.workflow_run.head_sha === run.head_sha;
  if (!hasMatchingArtifactCommit) throw new Error('Artifact commit does not match the source build.');
  const metadataArtifacts = artifacts.filter(item => item.name === 'launcher-build-metadata' && !item.expired);
  if (metadataArtifacts.length !== 1) throw new Error('Source build must have exactly one unexpired launcher-build-metadata artifact.');
  const metadata = metadataArtifacts[0];
  if (metadata.workflow_run?.head_sha && metadata.workflow_run.head_sha !== run.head_sha) throw new Error('Launcher metadata commit does not match the source build.');

  const source = {
    version,
    runId: Number(runId),
    sha: run.head_sha,
    artifactId: artifact.id,
    metadataArtifactId: metadata.id,
    runUrl: run.html_url,
    prerelease: isPrerelease(version),
  };
  await checkVersion({ github, context, version, sha: source.sha });
  return source;
}

async function loadReleaseFiles(directory) {
  const entries = await fs.readdir(directory);
  const unexpectedFiles = entries.filter(name => !RELEASE_FILES.includes(name) && name !== CHECKSUM_FILE);
  if (unexpectedFiles.length) throw new Error(`Unexpected release assets: ${unexpectedFiles.join(', ')}`);

  const files = [];
  for (const name of RELEASE_FILES) {
    const filename = path.join(directory, name);
    const stat = await fs.lstat(filename);
    if (!stat.isFile() || stat.size === 0) throw new Error(`Missing or empty release file: ${name}`);
    files.push({ name, data: await fs.readFile(filename) });
  }
  // Hash the same bytes that are uploaded; repacking or signing here would change
  // the already validated build rather than promote its original artifacts.
  const checksums = files.map(file => `${createHash('sha256').update(file.data).digest('hex')}  ${file.name}\n`).join('');
  const checksumData = Buffer.from(checksums, 'utf8');
  const checksumPath = path.join(directory, CHECKSUM_FILE);
  if (entries.includes(CHECKSUM_FILE)) await fs.unlink(checksumPath);
  await fs.writeFile(checksumPath, checksumData, { flag: 'wx' });
  files.push({ name: CHECKSUM_FILE, data: checksumData });
  return { files, checksums };
}

function releaseBody(source, repository, checksums) {
  return [
    `复用已成功的 [Windows Release 构建 ${source.runId}](${source.runUrl})，未重新构建。`,
    `源码提交：[${source.sha}](https://github.com/${repository}/commit/${source.sha})。`,
    '',
    '### 下载与使用 / Downloads',
    '- `ok-ww-personal-win32-setup.exe`：双版本启动器安装包，可选择 Official / Personal。',
    '- `ok-ww-personal-win32.zip`：便携版，解压后运行 `ok-ww-personal.exe`。',
    '- `SHA256SUMS.txt`：最终下载文件的 SHA-256 校验清单。',
    '',
    '### 文件校验 / SHA-256',
    '```text',
    checksums.trimEnd(),
    '```',
    'Windows：`Get-FileHash .\\ok-ww-personal-win32-setup.exe -Algorithm SHA256`，与上表对照。',
    'Linux：在下载目录运行 `sha256sum --check SHA256SUMS.txt`。',
    '',
    '个人双版本包未做 Authenticode 数字签名。SHA-256 用于核对文件完整性；文件与清单同时被替换时，不能单凭哈希验证发布者身份。',
    'Personal 从独立运行仓库通过 HTTPS 下载；旧 EXE 的内嵌来源不会自动迁移。停止应用后更换新启动器，再选择 Personal 安装，并核对 configs、多账号及自定义队伍。不要删除应用目录或 Python/pip 缓存。',
    '',
  ].join('\n');
}

async function publishRelease({ github, context, source, directory, metadataDirectory = 'launcher-metadata',
  root = path.resolve(__dirname, '../..'), runtimeOptions = {}, staged, dryRun = false,
  runtimePublisher = publishRuntime, migrationCheck = confirmMigration }) {
  validateVersion(source.version);
  const runtimeRepository = runtimeOptions.runtimeRepository ?? process.env.PERSONAL_RUNTIME_REPOSITORY;
  const runtimeBranch = runtimeOptions.runtimeBranch ?? process.env.PERSONAL_RUNTIME_BRANCH;
  validateConfiguration(runtimeRepository, runtimeBranch, root);
  const { files, checksums } = await loadReleaseFiles(directory);
  const launcherManifest = validateLauncher({ directory, metadataDirectory, source, root });
  const exported = staged || exportRuntime({ root, sourceSha: source.sha, version: source.version,
    output: path.join(root, 'build', `personal-runtime-${source.version}-${source.runId}`) });
  loadStage(exported.directory, exported.report.manifest_sha256);
  const runtimeInput = { ...runtimeOptions, root, version: source.version, businessSourceSha: source.sha,
    sourceBuildRunId: source.runId, runtimeRepository, runtimeBranch, stagedRuntimeDir: exported.directory,
    runtimeManifestHash: exported.report.manifest_sha256, launcherManifest,
    releaseAssetHashes: files.map(file => ({ name: file.name, bytes: file.data.length, sha256: hash(file.data) })) };
  if (exported.manifest.source.commit !== source.sha || exported.manifest.version !== source.version) throw new Error('Runtime stage does not match selected business build');
  if (dryRun) return runtimePublisher({ ...runtimeInput, dryRun: true });
  if (!runtimeInput.localRemote && !(runtimeOptions.token || process.env.PERSONAL_RUNTIME_TOKEN)) throw new Error('Missing PERSONAL_RUNTIME_TOKEN');
  runtimeInput.token = runtimeOptions.token || process.env.PERSONAL_RUNTIME_TOKEN;
  const identity = hash(json({ version: source.version, source: source.sha, buildRunId: source.runId,
    runtimeRepository, runtimeBranch, manifestHash: runtimeInput.runtimeManifestHash,
    launcherFingerprint: launcherManifest.fingerprint, assets: runtimeInput.releaseAssetHashes }));
  const marker = `<!-- personal-runtime:${identity} -->`;
  let receipt = { ...runtimeInput, token: undefined, root: undefined, stagedRuntimeDir: undefined,
    localRemote: undefined, launcherManifest: undefined, launcherSource: launcherManifest.source,
    publisher: { repository: `${context.repo.owner}/${context.repo.repo}`,
      commit: execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      inputs: fileHashes(root, ['.github/scripts/publish_existing_build.cjs', '.github/scripts/publish_personal_runtime.cjs',
        '.github/scripts/personal_release_contract.cjs', 'scripts/export_personal_runtime.cjs', 'scripts/personal_launcher_cache.cjs']) },
    phase: 'prepared', retryable: true };
  // Only explicit provenance is persisted; no environment, credentials or local paths.
  delete receipt.beforePush;
  const saveReceipt = async () => {
    const filename = path.join(root, 'build/personal-runtime-publication.json');
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, json(receipt));
    if (process.env.GITHUB_STEP_SUMMARY) await fs.appendFile(process.env.GITHUB_STEP_SUMMARY, `\nPersonal runtime publication\n\n\`\`\`json\n${json(receipt)}\`\`\`\n`);
  };
  try {
    let release = await getOrNull(() => github.rest.repos.getReleaseByTag({ ...context.repo, tag: source.version }));
    if (release && !release.body?.includes(marker)) throw new Error('Existing release/draft provenance or assets conflict');
    const tagExists = await checkVersion({ github, context, version: source.version, sha: source.sha });
    if (!tagExists) {
      // GITHUB_TOKEN-created refs do not trigger tag-push builds. Keep this workflow
      // on the built-in token so promoting an artifact never starts a rebuild.
      const { data: tag } = await github.rest.git.createTag({
        ...context.repo,
        tag: source.version,
        message: `Release ${source.version}\nSource build: ${source.runUrl}\nCommit: ${source.sha}`,
        object: source.sha,
        type: 'commit',
      });
      await github.rest.git.createRef({ ...context.repo, ref: `refs/tags/${source.version}`, sha: tag.sha });
    }

    // A release only becomes public after every asset is uploaded. Failed uploads
    // leave a draft for inspection, including when immutable releases are enabled.
    if (!release) ({ data: release } = await github.rest.repos.createRelease({
      ...context.repo,
      tag_name: source.version,
      target_commitish: source.sha,
      name: source.version,
      body: `${releaseBody(source, `${context.repo.owner}/${context.repo.repo}`, checksums)}\n${marker}`,
      draft: true,
      prerelease: source.prerelease,
    }));
    receipt.sourceReleaseUrl = release.html_url;
    receipt.phase = 'draft-assets';
    const existing = await github.paginate(github.rest.repos.listReleaseAssets, { ...context.repo, release_id: release.id, per_page: 100 });
    if (existing.some(asset => !files.some(file => file.name === asset.name)) || new Set(existing.map(asset => asset.name)).size !== existing.length) throw new Error('Unexpected/duplicate existing release assets');
    for (const file of files) {
      const previous = existing.find(asset => asset.name === file.name);
      if (previous) {
        let digest = previous.digest;
        if (!digest) {
          const response = await github.rest.repos.getReleaseAsset({ ...context.repo, asset_id: previous.id, headers: { accept: 'application/octet-stream' } });
          if (!(response.data instanceof ArrayBuffer) && !ArrayBuffer.isView(response.data)) throw new Error('Cannot verify existing release asset bytes');
          digest = `sha256:${hash(Buffer.from(response.data))}`;
        }
        if (previous.size !== file.data.length || digest !== `sha256:${hash(file.data)}`) throw new Error(`Existing asset hash conflict: ${file.name}`);
        continue;
      }
      if (!release.draft) throw new Error('Published release has missing assets');
      await github.rest.repos.uploadReleaseAsset({
        ...context.repo,
        release_id: release.id,
        name: file.name,
        data: file.data,
        headers: {
          'content-type': file.name === CHECKSUM_FILE ? 'text/plain; charset=utf-8' : 'application/octet-stream',
          'content-length': file.data.length,
        },
      });
    }
    receipt.phase = 'runtime-push';
    const runtime = await runtimePublisher({ ...runtimeInput, requireExistingTag: !release.draft });
    receipt = { ...receipt, ...runtime, phase: 'windows-migration-acceptance', retryable: true };
    await saveReceipt();
    if (!release.draft) {
      receipt.phase = 'complete'; receipt.retryable = false;
      await saveReceipt();
      return release;
    }
    receipt.migration = await migrationCheck({ github, approval: process.env.PERSONAL_RUNTIME_MIGRATION_APPROVAL, runtime, launcherManifest, root });
    receipt.phase = 'release-publication';
    ({ data: release } = await github.rest.repos.updateRelease({
      ...context.repo,
      release_id: release.id,
      draft: false,
      make_latest: source.prerelease ? 'false' : 'legacy',
    }));
    receipt.phase = 'complete'; receipt.retryable = false;
    await saveReceipt();
    return release;
  } catch (error) {
    receipt.error = error.message;
    receipt.retryable = !/conflict|mismatch|provenance/.test(error.message);
    await saveReceipt();
    throw error;
  }
}

module.exports = { validateSource, publishRelease, loadReleaseFiles, checkVersion };
