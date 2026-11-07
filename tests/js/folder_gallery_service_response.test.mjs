// Behavioural test for _serviceError() in folder-gallery-card.js (#405).
//
// art_delete / art_set_favourite are ENTITY services, and Home Assistant keys
// an entity service's response by entity id:
//   {"media_player.the_frame_pro": {"error": "no answer from the TV"}}
// 9.0.8 read result.response.error, found undefined, and toasted "Artwork
// deleted" for every answer. The card now reads the targeted entity's entry.
//
// The method is extracted from the card source and run against a stand-in,
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

const match = src.match(/\n  _serviceError\(result, entityId\) \{\n([\s\S]*?)\n  \}\n/);
assert.ok(match, '_serviceError(result, entityId) must exist in the card');
const serviceError = new Function('result', 'entityId', match[1]);

const TV = 'media_player.the_frame_pro';

// The real shape HA returns for an entity service: keyed by entity id.
{
  const result = { response: { [TV]: { error: 'no answer from the TV' } } };
  assert.equal(serviceError(result, TV), 'no answer from the TV');
}

// The refusal the reporter captured live.
{
  const result = {
    response: { [TV]: { error: 'Can only delete user-uploaded content (MY-*)' } },
  };
  assert.equal(
    serviceError(result, TV),
    'Can only delete user-uploaded content (MY-*)'
  );
}

// A success answer is not an error, so the success toast still shows.
{
  const result = { response: { [TV]: { success: true } } };
  assert.equal(serviceError(result, TV), null);
}

// A target given as a device or area resolves to another entity id: fall back
// to the single entry rather than silently reporting success.
{
  const result = { response: { 'media_player.other': { error: 'boom' } } };
  assert.equal(serviceError(result, TV), 'boom');
}

// Two entries and none of them ours: nothing we can attribute, so no error.
{
  const result = {
    response: { 'media_player.a': { error: 'a' }, 'media_player.b': { error: 'b' } },
  };
  assert.equal(serviceError(result, TV), null);
}

// No response at all (an older frontend ignoring returnResponse) is not an
// error: the call resolved, so the success toast is right.
{
  assert.equal(serviceError(undefined, TV), null);
  assert.equal(serviceError({}, TV), null);
  assert.equal(serviceError({ response: null }, TV), null);
}

// The flat shape 9.0.8 expected must not be mistaken for a working read: it is
// not what HA sends for an entity service, and it carries no entity entry.
{
  assert.equal(serviceError({ response: { error: 'flat' } }, TV), null);
}

console.log('folder_gallery_service_response: all assertions passed');
