// Behavioural test: the card with the integration's private Frame Art files
// (#472, 9.6.0).
//
// The thumbnails left www: the folder sensors publish `file_urls` next to
// `file_list`, and the card must use those URLs, not a /local/ URL derived
// from the path. `{{file_path}}` must be the file on disk (from file_list),
// not the URL with '/local/' replaced, which no longer contains it.
//
// _imagesForSource is extracted from the card source and run against a
// stand-in, with the pure helpers in scope, as the other card tests do.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(
  resolve(here, '../../custom_components/samsungtv_smart/www/folder-gallery-card.js'),
  'utf8'
);

const helpers = src.match(
  /\/\/ <fgc-pure-helpers>.*\r?\n([\s\S]*?)\/\/ <\/fgc-pure-helpers>/
)[1];

function method(signature) {
  const escaped = signature.replace(/[()]/g, (c) => `\\${c}`);
  const m = src.match(new RegExp(`\\n  ${escaped} \\{\\n([\\s\\S]*?)\\n  \\}\\n`));
  assert.ok(m, `${signature} must exist in the card`);
  return m[1];
}

const imagesForSource = new Function(
  'src',
  `${helpers}\n${method('_imagesForSource(src)')}`
);

const BASE = '/config/.storage/samsungtv_smart/frame_art/E1/personal';
const URLS = [
  '/api/samsungtv_smart/art/E1/personal/MY_F0001.jpg?token=t&v=1',
  '/api/samsungtv_smart/art/E1/personal/MY_F0002.jpg?token=t&v=2',
];
const card = {
  _hass: {
    states: {
      'sensor.tv_personal': {
        attributes: {
          path: `${BASE}/`,
          file_list: [`${BASE}/MY_F0001.jpg`, `${BASE}/MY_F0002.jpg`],
          file_urls: URLS,
        },
      },
      'sensor.www_photos': {
        attributes: {
          path: '/config/www/photos/',
          file_list: ['/config/www/photos/a.jpg'],
        },
      },
    },
  },
  _folderUrlBase(s) {
    return s.folder_sensor === 'sensor.www_photos' ? '/local/photos' : '';
  },
  _sensorBasePath(s) {
    return this._hass.states[s.folder_sensor].attributes.path;
  },
};

// The private files: each image takes its URL from file_urls, its file from file_list.
const priv = imagesForSource.call(card, { folder_sensor: 'sensor.tv_personal' });
assert.deepEqual(priv.map((i) => i.path), URLS);
assert.deepEqual(priv.map((i) => i.file), [`${BASE}/MY_F0001.jpg`, `${BASE}/MY_F0002.jpg`]);
assert.deepEqual(priv.map((i) => i.content_id), ['MY_F0001', 'MY_F0002']);

// A core folder sensor under www still gets /local/ URLs.
const www = imagesForSource.call(card, { folder_sensor: 'sensor.www_photos' });
assert.equal(www[0].path, '/local/photos/a.jpg');
assert.equal(www[0].file, '/config/www/photos/a.jpg');

// {{file_path}} uses the file when there is one (template substitution in the
// action dispatcher).
assert.ok(
  src.includes("imageData.file || imageData.path.replace('/local/', '/config/www/')"),
  '{{file_path}} must prefer the file from file_list'
);

console.log('folder_gallery_private_files: ok');
