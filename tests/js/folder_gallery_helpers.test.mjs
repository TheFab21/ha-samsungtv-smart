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
  `${region[1]}\nreturn { fgcBasename, fgcRelPath, fgcSubfolder, fgcGroupBySubfolder, fgcEscapeHtml, fgcResolveDefaultFolderKey, fgcLightboxButtons };`
);
const {
  fgcBasename,
  fgcRelPath,
  fgcSubfolder,
  fgcGroupBySubfolder,
  fgcEscapeHtml,
  fgcResolveDefaultFolderKey,
  fgcLightboxButtons,
} = factory();

// fgcEscapeHtml: folder labels go into <option> text unescaped otherwise.
assert.equal(fgcEscapeHtml('<3 & "Art"'), '&lt;3 &amp; &quot;Art&quot;');
assert.equal(fgcEscapeHtml('</option></select>'), '&lt;/option&gt;&lt;/select&gt;');
assert.equal(fgcEscapeHtml('plain'), 'plain');

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

// fgcResolveDefaultFolderKey: pick the starting folder set from a config value.
// `sets` here mirrors what fgcGroupBySubfolder returns ("All" first, then subs).
const dfSets = [
  { key: '__all__', label: 'All', images: [] },
  { key: 'abstract', label: 'abstract', images: [] },
  { key: 'landscapes', label: 'landscapes', images: [] },
  { key: 'nature/trees', label: 'trees', images: [] },
];
// exact key / label, case- and slash-insensitive
assert.equal(fgcResolveDefaultFolderKey(dfSets, 'landscapes'), 'landscapes');
assert.equal(fgcResolveDefaultFolderKey(dfSets, 'LANDSCAPES'), 'landscapes');
assert.equal(fgcResolveDefaultFolderKey(dfSets, '/landscapes/'), 'landscapes');
// nested: match the full path key, or just its last segment ("trees")
assert.equal(fgcResolveDefaultFolderKey(dfSets, 'nature/trees'), 'nature/trees');
assert.equal(fgcResolveDefaultFolderKey(dfSets, 'trees'), 'nature/trees');
// no match / empty / missing -> null so the caller keeps its first-set fallback
assert.equal(fgcResolveDefaultFolderKey(dfSets, 'does-not-exist'), null);
assert.equal(fgcResolveDefaultFolderKey(dfSets, ''), null);
assert.equal(fgcResolveDefaultFolderKey(dfSets, undefined), null);
assert.equal(fgcResolveDefaultFolderKey([], 'landscapes'), null);
// "folders:" list keys look like "<name>-<i>"; label is the friendly name.
const listSets = [
  { key: 'Favourites-0', label: 'Favourites', images: [] },
  { key: 'Personal-1', label: 'Personal', images: [] },
];
assert.equal(fgcResolveDefaultFolderKey(listSets, 'Personal'), 'Personal-1');

// fgcLightboxButtons: which lightbox buttons show for a gallery_type + content id.
// Regression guard (#415/#417 follow-up): auto mode must still offer Unfavourite
// on SAM- works, so a multi-folder gallery that mixes a favourites folder with
// other SAM- folders keeps the button per image.
const B = (gt, cid) => fgcLightboxButtons(gt, cid);
// auto (default): SAM- -> Select + Unfavourite; MY- -> Select + Delete; other -> Upload.
assert.deepEqual(B('auto', 'SAM-F0001'), { select: true, unfavourite: true, upload: false, delete: false });
assert.deepEqual(B(null, 'SAM_12345'), { select: true, unfavourite: true, upload: false, delete: false });
assert.deepEqual(B('auto', 'MY-0007'), { select: true, unfavourite: false, upload: false, delete: true });
assert.deepEqual(B('auto', 'local.jpg'), { select: false, unfavourite: false, upload: true, delete: false });
assert.deepEqual(B('auto', ''), { select: false, unfavourite: false, upload: true, delete: false });
// forced sets ignore the content id.
assert.deepEqual(B('favorites', 'MY-1'), { select: true, unfavourite: true, upload: false, delete: false });
assert.deepEqual(B('favourites', 'local.jpg'), { select: true, unfavourite: true, upload: false, delete: false });
assert.deepEqual(B('personal', 'SAM-1'), { select: true, unfavourite: false, upload: false, delete: true });
assert.deepEqual(B('upload', 'SAM-1'), { select: false, unfavourite: false, upload: true, delete: false });

console.log('folder_gallery_helpers: all assertions passed');
