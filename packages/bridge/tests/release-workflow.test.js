// Static assertions for the release/publish workflows. They have no
// unit-testable code, so these tests pin the gates that keep a
// mistagged tag push or a manual dispatch from publishing unreviewed
// or mismatched artifacts. Text assertions are deliberate — the repo
// has no YAML dependency and these strings are the contract.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const wf = (name) => readFileSync(join(root, '.github', 'workflows', name), 'utf8');

const TAG_PUSH_ONLY = "if: github.event_name == 'push' && github.ref_type == 'tag'";

test('release-bridge: release and fanout run only on tag push', () => {
  const y = wf('release-bridge.yml');
  assert.equal(y.split(TAG_PUSH_ONLY).length - 1, 2, 'expected the tag-push gate on both jobs');
});

test('release-bridge: fanout needs validate and release, uses reusable workflow', () => {
  const y = wf('release-bridge.yml');
  assert.match(y, /needs: \[validate, release\]/);
  assert.match(y, /uses: Icaro0310\/devin-powerups\/\.github\/workflows\/release\.yml@v1/);
});

test('release-bridge: triggers on bridge-v* tags only', () => {
  const y = wf('release-bridge.yml');
  assert.match(y, /tags:\s*\n\s*- "bridge-v\*"/);
});

test('npm publishers: dispatch requires a matching bridge-v tag on the commit', () => {
  for (const name of ['publish-bridge-npm.yml', 'publish-bridge-gpr.yml']) {
    const y = wf(name);
    assert.match(y, /if: github\.event_name == 'workflow_dispatch'/, name);
    assert.match(y, /bridge-v\$\(node -p "require\('\.\/package\.json'\)\.version"\)/, name);
    assert.match(y, /git\/refs\/tags\/\$want/, name);
  }
});

test('npm publishers: merged-origin guard present on both registries', () => {
  for (const name of ['publish-bridge-npm.yml', 'publish-bridge-gpr.yml']) {
    assert.match(wf(name), /compare\/main\.\.\.\$\{GITHUB_SHA\}/, name);
  }
});

test('npm publishers: dispatch checks the tag on THIS commit (GITHUB_SHA)', () => {
  for (const name of ['publish-bridge-npm.yml', 'publish-bridge-gpr.yml']) {
    const y = wf(name);
    assert.match(y, /git\/refs\/tags\/\$want/, name);
    assert.match(y, /GITHUB_SHA/, name);
  }
});

test('npm publishers: already-published versions skip instead of failing', () => {
  for (const name of ['publish-bridge-npm.yml', 'publish-bridge-gpr.yml']) {
    const y = wf(name);
    assert.match(y, /npm view/, name);
    assert.match(y, /steps\.dup\.outputs\.dup != 'true'/, name);
  }
});

test('release-bridge: dispatch only queues, publish outcomes are awaited', () => {
  const y = wf('release-bridge.yml');
  assert.match(y, /Wait for npm publishers/);
  assert.match(y, /gh run list --workflow "\$wf" --commit "\$SHA"/);
});

test('release: gpg fingerprint uses --show-keys without --import', () => {
  const y = wf('release.yml');
  assert.match(y, /gpg --batch --show-keys --with-colons/);
  assert.equal(y.includes('--import --show-keys'), false,
    'gpg rejects --import --show-keys as conflicting commands');
});
