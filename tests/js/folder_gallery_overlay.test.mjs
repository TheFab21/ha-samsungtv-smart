// Behavioural test for _liftOverlay() in folder-gallery-card.js.
//
// The lightbox and the Frame chooser are position:fixed with a high z-index,
// which only ranks them inside the card: in a Bubble Card pop-up the upload
// card placed after the gallery showed through the open lightbox. While an
// overlay is open the card raises itself above its siblings, and it restores
// its own inline style once both overlays are closed.
//
// The method is extracted from the card source and run against a stand-in
// element, as folder_gallery_helpers.test.mjs does for the pure helpers.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(
  resolve(here, '../../custom_components/samsungtv_smart/www/folder-gallery-card.js'),
  'utf8'
);

const match = src.match(/\n  _liftOverlay\(\) \{\n([\s\S]*?)\n  \}\n/);
assert.ok(match, '_liftOverlay() must exist in the card');
const liftOverlay = new Function(match[1]);

function card(style = {}) {
  const overlays = { lightbox: new Set(), 'frame-chooser': new Set() };
  const el = (id) => ({ classList: { contains: (c) => overlays[id].has(c) } });
  return {
    style: { position: '', zIndex: '', ...style },
    shadowRoot: { getElementById: (id) => el(id) },
    open(id) { overlays[id].add('open'); },
    close(id) { overlays[id].delete('open'); },
  };
}

// Opening the lightbox raises the card; closing it restores the card.
{
  const c = card();
  c.open('lightbox');
  liftOverlay.call(c);
  assert.equal(c.style.zIndex, '1000');
  assert.equal(c.style.position, 'relative');
  c.close('lightbox');
  liftOverlay.call(c);
  assert.equal(c.style.zIndex, '');
  assert.equal(c.style.position, '');
}

// A card that already had its own position / z-index gets them back.
{
  const c = card({ position: 'sticky', zIndex: '3' });
  c.open('frame-chooser');
  liftOverlay.call(c);
  assert.equal(c.style.position, 'sticky');
  assert.equal(c.style.zIndex, '1000');
  c.close('frame-chooser');
  liftOverlay.call(c);
  assert.equal(c.style.position, 'sticky');
  assert.equal(c.style.zIndex, '3');
}

// Stays raised while one overlay is still open; nothing to undo when idle.
{
  const c = card();
  c.open('lightbox');
  c.open('frame-chooser');
  liftOverlay.call(c);
  c.close('frame-chooser');
  liftOverlay.call(c);
  assert.equal(c.style.zIndex, '1000');
  c.close('lightbox');
  liftOverlay.call(c);
  assert.equal(c.style.zIndex, '');
  liftOverlay.call(c);
  assert.equal(c.style.zIndex, '');
}

console.log('folder-gallery overlay: ok');
