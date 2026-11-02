const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const ROOT = path.resolve(__dirname, '..');
const CLI = path.join(ROOT, 'node_modules/@commitlint/cli/cli.js');

function lint(message) {
  return spawnSync(process.execPath, [CLI, '--config', path.join(ROOT, 'commitlint.config.cjs')], {
    cwd: ROOT, input: message, encoding: 'utf8',
  });
}

for (const message of [
  'feat: add commit lint',
  'fix(combat): 修复技能冷却识别',
  'feat(config)!: change configuration format\n\nBREAKING CHANGE: new configuration format',
  'merge: integrate personal changes',
  'merge(personal): integrate upstream changes',
  "Merge branch 'feature' into personal",
  "Merge remote-tracking branch 'upstream/master'",
  'Merge pull request #123 from contributor/feature',
  "Merge branch '" + 'a'.repeat(100) + "'",
]) {
  test(`accepts ${message.split('\n')[0]}`, () => {
    const result = lint(message);
    assert.equal(result.status, 0, result.stderr || result.stdout);
  });
}

for (const message of [
  'update code', 'unknown: add lint', 'merge:', 'merge: ',
  'feat: Add lint', 'feat: add lint.', 'feat: ' + 'a'.repeat(95),
]) {
  test(`rejects ${message}`, () => {
    const result = lint(message);
    assert.equal(result.status, 1, result.stderr || result.stdout);
  });
}

function fixture(t) {
  const tempRoot = path.join(ROOT, 'build/commitlint-tests');
  fs.mkdirSync(tempRoot, { recursive: true });
  const directory = fs.mkdtempSync(path.join(tempRoot, 'repo-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(directory)), tempRoot);
    assert.ok(path.basename(directory).startsWith('repo-'));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const env = { ...process.env };
  // Tests create and commit only in an isolated fixture, even when invoked
  // from Git hooks that export the developer's repository and index paths.
  for (const key of [
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_CONFIG',
    'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'HUSKY', 'CI', 'NODE_ENV',
  ]) delete env[key];
  const emptyConfig = path.join(directory, 'empty-gitconfig');
  fs.writeFileSync(emptyConfig, '');
  env.GIT_CONFIG_NOSYSTEM = '1';
  env.GIT_CONFIG_GLOBAL = emptyConfig;
  env.XDG_CONFIG_HOME = path.join(directory, '.config');
  const pathKey = Object.keys(env).find(key => key.toLowerCase() === 'path') || 'PATH';
  env[pathKey] = [path.dirname(process.execPath), path.join(ROOT, 'node_modules/.bin'), env[pathKey]].join(path.delimiter);

  function run(command, args, extraEnv = {}) {
    return spawnSync(command, args, { cwd: directory, env: { ...env, ...extraEnv }, encoding: 'utf8' });
  }
  function git(...args) {
    const result = run('git', args);
    assert.equal(result.status, 0, result.error?.message || result.stderr || result.stdout);
    return result.stdout.trim();
  }
  git('init', '--initial-branch=main');
  git('config', 'user.name', 'Commit Lint Test');
  git('config', 'user.email', 'commit-lint@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  for (const relative of ['.husky/commit-msg', 'scripts/prepare-husky.cjs', 'commitlint.config.cjs', 'package.json']) {
    const target = path.join(directory, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(path.join(ROOT, relative), target);
  }
  return { directory, run, git };
}

test('installed hook rejects invalid commits and permits real merges', t => {
  const { run, git } = fixture(t);
  const prepared = run(process.execPath, ['scripts/prepare-husky.cjs']);
  assert.equal(prepared.status, 0, prepared.stderr);
  assert.equal(git('config', '--local', '--get', 'core.hooksPath'), '.husky/_');
  git('commit', '--allow-empty', '-m', 'chore: initial commit');
  const before = git('rev-parse', 'HEAD');
  const invalid = run('git', ['commit', '--allow-empty', '-m', 'update code']);
  assert.notEqual(invalid.status, 0);
  assert.match(invalid.stdout + invalid.stderr, /subject-empty|type-empty/);
  assert.equal(git('rev-parse', 'HEAD'), before);

  for (const [branch, title] of [['feature-auto', null], ['feature-manual', 'merge: integrate feature']]) {
    git('switch', '-c', branch);
    git('commit', '--allow-empty', '-m', 'feat: add feature');
    git('switch', 'main');
    const args = ['merge', '--no-ff', '--no-edit', branch];
    if (title) args.push('-m', title);
    git(...args);
    assert.equal(git('rev-list', '--parents', '-n', '1', 'HEAD').split(' ').length, 3);
  }
});

test('prepare isolates hooks when Git worktree configuration is enabled', t => {
  const { run, git } = fixture(t);
  git('config', '--local', 'extensions.worktreeConfig', 'true');
  git('config', '--local', 'core.hooksPath', 'existing-hooks');
  const prepared = run(process.execPath, ['scripts/prepare-husky.cjs']);
  assert.equal(prepared.status, 0, prepared.stderr);
  assert.equal(git('config', '--local', '--get', 'core.hooksPath'), 'existing-hooks');
  assert.equal(git('config', '--worktree', '--get', 'core.hooksPath'), '.husky/_');
});

test('prepare skips hook installation in CI, production, and HUSKY=0', t => {
  const { directory, run } = fixture(t);
  for (const env of [{ CI: 'true' }, { NODE_ENV: 'production' }, { HUSKY: '0' }]) {
    const result = run(process.execPath, ['scripts/prepare-husky.cjs'], env);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(fs.existsSync(path.join(directory, '.husky/_')), false);
    assert.equal(run('git', ['config', '--get', 'core.hooksPath']).status, 1);
  }
});
