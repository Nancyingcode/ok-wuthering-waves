module.exports = {
  extends: ['@commitlint/config-conventional'],
  defaultIgnores: true,
  rules: {
    'type-enum': [2, 'always', [
      'build', 'chore', 'ci', 'docs', 'feat', 'fix', 'merge', 'perf',
      'refactor', 'revert', 'style', 'test',
    ]],
  },
};
