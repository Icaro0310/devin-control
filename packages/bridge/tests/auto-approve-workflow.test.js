// Static assertions for .github/workflows/auto-approve.yml. The
// caller is thin by design (triggers + pin to the reusable pr-approve
// gate in devin-powerups), so these tests pin the v1.6 caller
// contract — the gate version, the pull_request_review_comment
// trigger that re-evaluates the gate on fix-commit replies, and the
// scoped job permissions. Text assertions are deliberate — the repo
// has no YAML dependency and these strings are the contract.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const wf = (name) => readFileSync(join(root, '.github', 'workflows', name), 'utf8');

test('auto-approve: gate pin is exactly v1.6', () => {
  const y = wf('auto-approve.yml');
  assert.match(
    y,
    /uses: Icaro0310\/devin-powerups\/\.github\/workflows\/pr-approve\.yml@v1\.6/,
    'v1.6 adds cross-repo fix anchors and gate-eligible concurrency; a silent downgrade must fail here',
  );
});

test('auto-approve: pull_request_review_comment trigger re-evaluates the gate', () => {
  const y = wf('auto-approve.yml');
  assert.match(y, /  pull_request_review_comment:\s*\n\s*types: \[created\]/);
});

test('auto-approve: job permissions stay scoped', () => {
  const y = wf('auto-approve.yml');
  const approve = y.split('  approve:')[1];
  assert.match(approve, /contents: read/);
  assert.match(approve, /pull-requests: write/);
  assert.doesNotMatch(approve, /contents: write/);
});
