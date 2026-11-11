"""Execute the actual progressive-enhancement asset with controlled browser responses."""
import shutil
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe.webassets import JAVASCRIPT

HARNESS = r"""
const assert = require('node:assert/strict');
const listeners = {}, windowListeners = {};
class Element {
  constructor() { this.children = []; this.dataset = {}; this.hidden = false; this.isConnected = true; }
  append(node) { this.children.push(node); }
  replaceChildren() { this.children = []; }
  setAttribute() {}
  removeAttribute() {}
  getBoundingClientRect() { return {top: 100, bottom: 150}; }
  scrollIntoView() {}
  focus() {}
}
let result = new Element();
const field = {value: 'old'};
const hash = {value: 'old-hash'};
const button = {disabled: false};
const form = {
  action: 'http://localhost/api/actions/source-patch', dataset: {},
  hasAttribute: () => false,
  closest: () => null, append: () => {},
  reportValidity: () => true, setAttribute: () => {}, removeAttribute: () => {},
  querySelectorAll: selector => selector.startsWith('button') ? [button] : [],
  elements: {namedItem: name => name === 'base_hash' ? hash : null}
};
global.location = {origin: 'http://localhost', pathname: '/config', search: ''};
global.document = {
  getElementById: id => id === 'action-result' ? result : null,
  createElement: tag => { const node = new Element(); if (tag === 'div') result = node; return node; },
  querySelectorAll: () => [], querySelector: () => null,
  addEventListener: (name, fn) => { listeners[name] = fn; }, body: {dataset: {viewPath: '/view'}}
};
global.window = {
  addEventListener: (name, fn) => { windowListeners[name] = fn; },
  confirm: () => true, scrollY: 0, scrollTo: () => {}
};
global.DOMParser = class { parseFromString() { return {querySelectorAll: () => []}; } };
global.FormData = class {
  constructor() { this.rows = [['f.feed_label', field.value], ['base_hash', hash.value]]; }
  *[Symbol.iterator]() { yield* this.rows; }
};
let release, submitted, postCount = 0, readCount = 0;
global.fetch = (url, options) => {
  if (options.method === 'POST') {
    postCount++;
    submitted = options.body.get('f.feed_label');
    return new Promise(resolve => { release = envelope => resolve({json: async () => envelope}); });
  }
  readCount++;
  return Promise.resolve({ok: true, headers: {get: () => 'text/html'}, text: async () => '<html></html>'});
};
const edit = (value, event = 'input') => {
  field.value = value;
  listeners[event]({target: {closest: selector => selector.startsWith('form') ? form : null}});
};
const warns = () => {
  let warned = false;
  windowListeners.beforeunload({preventDefault: () => { warned = true; }});
  return warned;
};
const submit = () => listeners.submit({target: form, preventDefault: () => {}});
const success = {ok: true, changed: true, data: {hash: 'new-hash'}};
"""


NODE = shutil.which("node")


@unittest.skipUnless(NODE or os.environ.get("NEWSPIPE_REQUIRE_JS_TESTS") == "1",
                     "Node is needed for optional JavaScript behavioral tests")
class WorkbenchJavaScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise RuntimeError("Node is required by NEWSPIPE_REQUIRE_JS_TESTS=1; JavaScript coverage cannot be skipped")

    def run_javascript(self, scenario):
        script = HARNESS + JAVASCRIPT + "\n(async () => {\n" + scenario + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run([NODE, "-"], input=script, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_success_acknowledges_only_submitted_revision(self):
        self.run_javascript(r"""
edit('submitted');
const pending = submit();
assert.equal(button.disabled, true);
edit('edited while saving', 'change');
assert.equal(warns(), true);
release(success);
await pending;
assert.equal(submitted, 'submitted');
assert.equal(field.value, 'edited while saving');
assert.equal(warns(), true, 'new edits must still warn before leaving');
assert.equal(result.children.some(node => node.textContent?.includes('新编辑尚未保存')), true);
assert.equal(hash.value, 'new-hash');
assert.equal(button.disabled, false);
assert.equal(postCount, 1);
""")

    def test_success_clears_warning_when_revision_is_unchanged(self):
        self.run_javascript(r"""
edit('submitted');
const pending = submit();
release(success);
await pending;
assert.equal(warns(), false);
assert.equal(hash.value, 'new-hash');
assert.equal(button.disabled, false);
""")

    def test_failed_save_keeps_new_inputs_dirty_without_retry(self):
        self.run_javascript(r"""
edit('submitted');
const pending = submit();
edit('new edits');
release({ok: false, error: {code: 'E_CONFLICT', message: 'stale', hint: 'refresh'}});
await pending;
assert.equal(field.value, 'new edits');
assert.equal(warns(), true);
assert.equal(hash.value, 'old-hash');
assert.equal(postCount, 1);
assert.equal(button.disabled, false);
""")

    def test_preview_never_acknowledges_unsaved_revision(self):
        self.run_javascript(r"""
form.action = 'http://localhost/api/actions/notification-preview';
edit('candidate');
const pending = submit();
release({ok: true, changed: false, data: {sources: []}});
await pending;
assert.equal(warns(), true);
assert.equal(hash.value, 'old-hash');
""")

    def test_probe_success_never_acknowledges_or_refreshes_even_with_a_hash(self):
        self.run_javascript(r"""
form.action = 'http://localhost/api/actions/rsshub-probe';
edit('candidate');
const pending = submit();
release({ok: true, changed: false, data: {hash: 'irrelevant', message: 'reachable', checked_url: 'https://example.invalid', http_status: 200}});
await pending;
assert.equal(warns(), true);
assert.equal(hash.value, 'old-hash');
assert.equal(readCount, 0);
assert.equal(result.children.some(node => node.textContent?.includes('只读检测')), true);
""")

    def test_failed_probe_and_transport_are_not_unknown_writes(self):
        self.run_javascript(r"""
form.action = 'http://localhost/api/actions/rsshub-probe';
edit('candidate');
let pending = submit();
release({ok: false, error: {code: 'E_NETWORK', message: 'unreachable', hint: 'check', details: {http_status: 404}}});
await pending;
assert.equal(result.children.some(node => node.textContent?.includes('未应用')), false);
assert.equal(result.children.some(node => node.textContent?.includes('只读检测')), true);
global.fetch = async () => { postCount++; throw new Error('offline'); };
await submit();
assert.equal(result.children.some(node => node.textContent?.includes('检测结果未确认')), true);
assert.equal(result.children.some(node => node.textContent?.includes('写入')), false);
assert.equal(warns(), true);
assert.equal(hash.value, 'old-hash');
assert.equal(readCount, 0);
assert.equal(postCount, 2);
""")
