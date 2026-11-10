// Behavioural test for the lightbox target / action and the gesture runner in
// folder-gallery-card.js (#416).
//
// Frame_Art_Gallery.md documents "tap: lightbox, double_tap: perform_action"
// with no `action:` key. The lightbox buttons read `action:` only, so Delete
// called art_delete with target.entity_id '' ("expected 'all' or 'none'") and
// Select did nothing. And `double_tap_action: lightbox` did nothing at all:
// 'lightbox' was understood for tap_action only.
//
// The methods are extracted from the card source and run against a stand-in,
// as folder_gallery_overlay.test.mjs does.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(
  resolve(here, '../../custom_components/samsungtv_smart/www/folder-gallery-card.js'),
  'utf8'
);

function method(signature, ...params) {
  const escaped = signature.replace(/[()]/g, (c) => `\\${c}`);
  const m = src.match(new RegExp(`\\n  ${escaped} \\{\\n([\\s\\S]*?)\\n  \\}\\n`));
  assert.ok(m, `${signature} must exist in the card`);
  return new Function(...params, m[1]);
}

const configuredActions = method('_configuredActions()');
const getEntityId = method('_getEntityId()');
const runLightboxAction = method('_runLightboxAction(imageData, kind)', 'imageData', 'kind');
const runGesture = method('_runGesture(imageData, gesture)', 'imageData', 'gesture');
const actionKind = method('_actionKind(action)', 'action');
const buildAction = method('_buildAction(kind, tvEntity)', 'kind', 'tvEntity');

const TV = 'media_player.frame';
const IMG = { content_id: 'MY_F0001' };
const select = (entity) => ({
  action: 'perform-action',
  perform_action: 'samsungtv_smart.art_select_image',
  target: entity ? { entity_id: entity } : undefined,
  data: { content_id: '{{content_id}}' },
});

function card(config) {
  const calls = { executed: [], toasts: [], opened: [], moreInfo: [] };
  const c = {
    _config: config,
    _t: (key, vars) => (vars ? `${key}:${vars.msg}` : key),
    executeAction: (img, action) => calls.executed.push(action),
    showToast: (msg, isError) => calls.toasts.push([msg, isError]),
    openLightbox: (img) => calls.opened.push(img),
    fireEvent: (type, detail) => calls.moreInfo.push(detail),
    calls,
  };
  c._configuredActions = configuredActions.bind(c);
  c._getEntityId = getEntityId.bind(c);
  return c;
}

// The documented layout: the lightbox buttons find the double-tap target.
{
  const c = card({ tap_action: 'lightbox', double_tap_action: select(TV) });
  assert.equal(c._getEntityId(), TV);
  runLightboxAction.call(c, IMG, 'select');
  assert.deepEqual(c.calls.executed, [select(TV)]);
}

// The legacy `action:` block keeps priority, whatever it calls.
{
  const legacy = {
    service: 'media_player.play_media',
    target: { entity_id: 'media_player.other' },
  };
  const c = card({ tap_action: 'lightbox', action: legacy, double_tap_action: select(TV) });
  assert.equal(c._getEntityId(), 'media_player.other');
  runLightboxAction.call(c, IMG, 'select');
  assert.deepEqual(c.calls.executed, [legacy]);
}

// No action anywhere, but a Frame TV entity: the standard call is built.
{
  const c = card({ tap_action: 'lightbox', frame_tv_entity: TV });
  runLightboxAction.call(c, IMG, 'select');
  assert.deepEqual(c.calls.executed, [
    {
      perform_action: 'samsungtv_smart.art_select_image',
      target: { entity_id: TV },
      data: { content_id: '{{content_id}}' },
    },
  ]);
}

// Upload galleries look for art_upload, and build it the same way.
{
  const upload = {
    perform_action: 'samsungtv_smart.art_upload',
    target: { entity_id: TV },
    data: { file_path: '{{file_path}}' },
  };
  const c = card({ tap_action: 'lightbox', hold_action: upload, double_tap_action: select(TV) });
  runLightboxAction.call(c, IMG, 'upload');
  assert.deepEqual(c.calls.executed, [upload]);
}

// No TV anywhere: say so, and call nothing (no more target.entity_id '').
{
  const c = card({ tap_action: 'lightbox' });
  assert.equal(c._getEntityId(), '');
  runLightboxAction.call(c, IMG, 'select');
  assert.deepEqual(c.calls.executed, []);
  assert.deepEqual(c.calls.toasts, [['error:no_tv', true]]);
}

// A data.entity_id is a target too.
{
  const c = card({
    double_tap_action: { perform_action: 'samsungtv_smart.art_select_image', data: { entity_id: TV } },
  });
  assert.equal(c._getEntityId(), TV);
}

// Gestures: 'lightbox' opens the preview from a double tap or a long press.
{
  const c = card({ tap_action: select(TV), double_tap_action: 'lightbox' });
  runGesture.call(c, IMG, 'lightbox');
  assert.deepEqual(c.calls.opened, [IMG]);
  runGesture.call(c, IMG, select(TV));
  assert.deepEqual(c.calls.executed, [select(TV)]);
  runGesture.call(c, IMG, { action: 'none' });
  runGesture.call(c, IMG, null);
  assert.equal(c.calls.executed.length, 1);
  assert.equal(c.calls.opened.length, 1);
}

// The editor offers and reads back the preview on any gesture.
{
  assert.equal(actionKind.call({}, 'lightbox'), 'lightbox');
  assert.equal(buildAction.call({}, 'lightbox', TV), 'lightbox');
  assert.equal(actionKind.call({}, select(TV)), 'select');
  assert.equal(actionKind.call({}, null), 'none');
}

// The double tap and the long press go through the gesture runner.
assert.ok(src.includes('this._runGesture(this._images[index], this._config.double_tap_action);'));
assert.ok(src.includes('this._runGesture(imageData, this._config.hold_action);'));

console.log('folder_gallery_gestures: all assertions passed');
