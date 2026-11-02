const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const path = require('node:path');

const BUILD_WORKFLOW = '.github/workflows/build.yml';
const ARTIFACT_NAME = 'release-assets';
const RELEASE_FILES = ['ok-ww-personal-win32-setup.exe', 'ok-ww-personal-win32.zip'];
const CHECKSUM_FILE = 'SHA256SUMS.txt';
const VERSION_PATTERN = /^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$/;

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
  if (existingRelease) {
    throw new Error(`Release ${version} already exists. Choose a new version or remove a failed draft before retrying.`);
  }
  const taggedCommit = await getOrNull(() => github.rest.repos.getCommit({
    ...context.repo, ref: `refs/tags/${version}`,
  }));
  if (taggedCommit && taggedCommit.sha !== sha) {
    throw new Error(`Tag ${version} points to ${taggedCommit.sha}, not source build commit ${sha}.`);
  }
  return taggedCommit !== null;
}

async function validateSource({ github, context, version, runId }) {
  version = version.trim();
  runId = String(runId).trim();
  if (!VERSION_PATTERN.test(version)) {
    throw new Error('Version must be vMAJOR.MINOR.PATCH, optionally followed by a prerelease such as -beta.1.');
  }
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

  const source = {
    version,
    runId: Number(runId),
    sha: run.head_sha,
    artifactId: artifact.id,
    runUrl: run.html_url,
    prerelease: version.includes('-'),
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
    '首次安装所选来源仍需联网；Personal 来源需要可访问 GitHub 的 SSH agent。',
    '',
  ].join('\n');
}

async function publishRelease({ github, context, source, directory }) {
  const { files, checksums } = await loadReleaseFiles(directory);
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
  const { data: draft } = await github.rest.repos.createRelease({
    ...context.repo,
    tag_name: source.version,
    target_commitish: source.sha,
    name: source.version,
    body: releaseBody(source, `${context.repo.owner}/${context.repo.repo}`, checksums),
    draft: true,
    prerelease: source.prerelease,
  });
  for (const file of files) {
    await github.rest.repos.uploadReleaseAsset({
      ...context.repo,
      release_id: draft.id,
      name: file.name,
      data: file.data,
      headers: {
        'content-type': file.name === CHECKSUM_FILE ? 'text/plain; charset=utf-8' : 'application/octet-stream',
        'content-length': file.data.length,
      },
    });
  }
  const { data: release } = await github.rest.repos.updateRelease({
    ...context.repo,
    release_id: draft.id,
    draft: false,
    make_latest: source.prerelease ? 'false' : 'legacy',
  });
  return release;
}

module.exports = { validateSource, publishRelease };
