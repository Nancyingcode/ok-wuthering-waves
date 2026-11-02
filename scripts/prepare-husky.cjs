const { copyFileSync, existsSync, mkdirSync, writeFileSync } = require('node:fs');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');

if (process.env.HUSKY === '0' || process.env.CI || process.env.NODE_ENV === 'production' || !existsSync('.git')) {
  process.exit(0);
}

const hookDirectory = path.join('.husky', '_');
mkdirSync(hookDirectory, { recursive: true });
writeFileSync(path.join(hookDirectory, '.gitignore'), '*\n');
copyFileSync(path.join(path.dirname(require.resolve('husky')), 'husky'), path.join(hookDirectory, 'h'));
writeFileSync(path.join(hookDirectory, 'commit-msg'), '#!/usr/bin/env sh\n. "$(dirname "$0")/h"\n', { mode: 0o755 });

const worktreeConfig = spawnSync('git', ['config', '--bool', '--get', 'extensions.worktreeConfig'], {
  encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'],
});
if (worktreeConfig.error) throw worktreeConfig.error;
if (worktreeConfig.status !== 0 && worktreeConfig.status !== 1) {
  throw new Error('Unable to read Git worktree configuration.');
}
// The repository has concurrent worktrees. Keep hook activation local to this
// worktree when supported, so older branches keep their existing hooks.
const scope = worktreeConfig.stdout.trim() === 'true' ? '--worktree' : '--local';
execFileSync('git', ['config', scope, 'core.hooksPath', '.husky/_'], { stdio: 'inherit' });
