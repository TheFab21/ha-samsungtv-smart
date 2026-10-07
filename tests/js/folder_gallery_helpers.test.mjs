// Behavioural test for the pure helpers in folder-gallery-card.js.
//
// The card is a browser module (customElements, HTMLElement) that cannot be
// imported in Node, so we extract the delimited <fgc-pure-helpers> region and
// evaluate just that. Run directly (`node folder_gallery_helpers.test.mjs`) or
// via tests/test_folder_gallery_card.py, which skips when Node is absent.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const cardPath = resolve(
  here,
  '../../custom_components/samsungtv_smart/www/folder-gallery-card.js'
);
const src = readFileSync(cardPath, 'utf8');

const region = src.match(
  /\/\/ <fgc-pure-helpers>.*\r?\n([\s\S]*?)\/\/ <\/fgc-pure-helpers>/
);
assert.ok(region, 'the <fgc-pure-helpers> region must exist in the card');

const factory = new Function(
  `${region[1]}\nreturn { fgcBasename, fgcRelPath, fgcSubfolder, fgcGroupBySubfolder };`
);
const { fgcBasename, fgcRelPath, fgcSubfolder, fgcGroupBySubfolder } = factory();

// fgcBasename
assert.equal(fgcBasename('/a/b/c.jpg'), 'c.jpg');
assert.equal(fgcBasename('c.jpg'), 'c.jpg');

// fgcRelPath: path relative to the sensor base, always leading "/"
const base = '/config/www/frame_art/E1/personal';
assert.equal(fgcRelPath(`${base}/img.jpg`, base), '/img.jpg');
assert.equal(fgcRelPath(`${base}/nature/trees/oak.jpg`, base), '/nature/trees/oak.jpg');
assert.equal(fgcRelPath(`${base}/img.jpg`, base + '/'), '/img.jpg'); // trailing slash
assert.equal(fgcRelPath('/elsewhere/x.jpg', base), '/x.jpg'); // no match -> basename

// fgcSubfolder
assert.equal(fgcSubfolder('/img.jpg'), '');
assert.equal(fgcSubfolder('/nature/oak.jpg'), 'nature');
assert.equal(fgcSubfolder('/nature/trees/oak.jpg'), 'nature/trees');

// fgcGroupBySubfolder
const flat = [
  { subfolder: '', name: 'a' },
  { subfolder: '', name: 'b' },
];
assert.deepEqual(fgcGroupBySubfolder(flat, 'All'), [], 'flat folder -> no selector');

const mixed = [
  { subfolder: '', name: 'root1' },
  { subfolder: 'landscapes', name: 'l1' },
  { subfolder: 'landscapes', name: 'l2' },
  { subfolder: 'abstract', name: 'a1' },
];
const sets = fgcGroupBySubfolder(mixed, 'All');
assert.equal(sets[0].key, '__all__');
assert.equal(sets[0].label, 'All');
assert.equal(sets[0].images.length, 4, 'All holds every image');
assert.deepEqual(
  sets.slice(1).map((s) => s.key),
  ['abstract', 'landscapes'],
  'sub-folders sorted'
);
assert.equal(sets.find((s) => s.key === 'landscapes').images.length, 2);
assert.equal(sets.find((s) => s.key === 'abstract').images.length, 1);

// Nested labels use the last path segment.
const nested = [{ subfolder: 'nature/trees', name: 'x' }];
assert.equal(fgcGroupBySubfolder(nested, 'All')[1].label, 'trees');

console.log('folder_gallery_helpers: all assertions passed');
