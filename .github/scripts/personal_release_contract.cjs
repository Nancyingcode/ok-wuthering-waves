const fs = require('node:fs');
const path = require('node:path');
const { fingerprint, fileHashes, validateCache, RELEASE_FILES, serialize } = require('../../scripts/personal_launcher_cache.cjs');
const { SOURCE_REPOSITORY, hash, validateVersion, isPrerelease } = require('../../scripts/export_personal_runtime.cjs');
const { RUNTIME_REPOSITORY, validateConfiguration } = require('./publish_personal_runtime.cjs');

function validateLauncher({ directory, metadataDirectory, source, root = path.resolve(__dirname, '../..') }) {
  const read = name => JSON.parse(fs.readFileSync(path.join(metadataDirectory, `${name}.json`), 'utf8').replace(/^\uFEFF/, ''));
  const manifest = read('manifest');
  const report = read('report');
  const repository = SOURCE_REPOSITORY;
  const runUrl = `https://github.com/${repository}/actions/runs/${source.runId}`;
  if (report.currentSha !== source.sha || report.runUrl !== runUrl) throw new Error('Launcher metadata is not bound to the selected business build run');
  if (manifest.source?.repository !== repository || !/^[0-9a-f]{40}$/.test(manifest.source.sha) ||
      !Number.isSafeInteger(Number(manifest.source.runId)) || Number(manifest.source.runId) < 1) throw new Error('Invalid original launcher build provenance');
  const expected = fingerprint({ ...manifest.inputs, tools: manifest.buildTools });
  const valid = validateCache(directory, expected, manifest);
  if (!valid.valid) throw new Error(`Incompatible launcher: ${valid.reason}`);
  const approved = fileHashes(root, ['launcher/pyappify.personal.yml', 'launcher/pyappify-profiles.patch']);
  const recorded = manifest.inputs?.prepared?.files;
  for (const [name, digest] of Object.entries(approved)) {
    if (recorded?.[name] !== digest) throw new Error(`Launcher migration input mismatch: ${name}; choose a compatible new build`);
  }
  return manifest;
}

async function confirmMigration({ github, approval, runtime, launcherManifest, root = path.resolve(__dirname, '../..') }) {
  if (!approval) throw new Error('Runtime published; source release remains draft: PERSONAL_RUNTIME_MIGRATION_APPROVAL is missing (Windows cold-install acceptance)');
  const record = JSON.parse(approval);
  validateVersion(record.version);
  const inputs = fileHashes(root, ['launcher/pyappify.personal.yml', 'launcher/pyappify-profiles.patch']);
  const checks = ['coldInstall', 'pipCheck', 'imports', 'gui', 'templates', 'ocr', 'echo', 'sourceSwitch', 'userData'];
  const hasProof = record.runtimeRepository === RUNTIME_REPOSITORY && !isPrerelease(record.version) &&
    /^[0-9a-f]{40}$/.test(record.runtimeCommit) && /^[0-9a-f]{40}$/.test(record.businessSourceSha) &&
    /^[0-9a-f]{64}$/.test(record.manifestHash) && /^[0-9a-f]{64}$/.test(record.payloadHash) &&
    serialize(record.inputs) === serialize(inputs) && serialize(record.launcherArtifacts) === serialize(launcherManifest.artifacts) &&
    checks.every(key => record.checks?.[key] === true) && /^https:\/\/github\.com\//.test(record.evidenceUrl || '');
  if (!hasProof) throw new Error('Windows migration approval is incomplete or belongs to other launcher inputs/artifacts');
  const [owner, repo] = RUNTIME_REPOSITORY.split('/');
  const { data: commit } = await github.rest.repos.getCommit({ owner, repo, ref: `refs/tags/${record.version}` });
  const { data: content } = await github.rest.repos.getContent({ owner, repo, path: 'runtime-manifest.json', ref: record.version });
  if (content.encoding !== 'base64') throw new Error('Migration runtime manifest unavailable');
  const bytes = Buffer.from(content.content, 'base64');
  const manifest = JSON.parse(bytes);
  if (commit.sha !== record.runtimeCommit || hash(bytes) !== record.manifestHash || manifest.version !== record.version ||
      manifest.source.commit !== record.businessSourceSha || manifest.payload_sha256 !== record.payloadHash) throw new Error('Migration acceptance does not match the remote stable runtime tag');
  return { version: record.version, evidenceUrl: record.evidenceUrl, currentRuntimeCommit: runtime.runtimeCommit };
}

module.exports = { validateLauncher, confirmMigration, RELEASE_FILES, validateConfiguration };
