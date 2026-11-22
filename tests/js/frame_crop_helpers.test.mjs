// Behavioural test for the pure geometry helpers in frame-crop-upload-card.js.
//
// The card is a browser module (customElements, HTMLElement) that cannot be
// imported in Node, so we extract the delimited <fcu-pure-helpers> region and
// evaluate just that. Run directly (`node frame_crop_helpers.test.mjs`) or via
// tests/test_frame_crop_upload_card.py, which skips when Node is absent.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const cardPath = resolve(
  here,
  '../../custom_components/samsungtv_smart/www/frame-crop-upload-card.js'
);
const src = readFileSync(cardPath, 'utf8');

const region = src.match(
  /\/\/ <fcu-pure-helpers>.*\r?\n([\s\S]*?)\/\/ <\/fcu-pure-helpers>/
);
assert.ok(region, 'the <fcu-pure-helpers> region must exist in the card');

const {
  fcuParseResolution,
  fcuRotatedSize,
  fcuCoverScale,
  fcuContainScale,
  fcuClampCentre,
  fcuZoomAbout,
  fcuSliderToScale,
  fcuScaleToSlider,
  fcuCroppedName,
} = new Function(
  `${region[1]}\nreturn { fcuParseResolution, fcuRotatedSize, fcuCoverScale, fcuContainScale, fcuClampCentre, fcuZoomAbout, fcuSliderToScale, fcuScaleToSlider, fcuCroppedName };`
)();

const close = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} != ${b}`);

// fcuParseResolution: always landscape, 4K fallback for missing or bad values.
assert.deepEqual(fcuParseResolution('1920x1080'), { w: 1920, h: 1080 });
assert.deepEqual(fcuParseResolution('1080x1920'), { w: 1920, h: 1080 }, 'portrait string');
assert.deepEqual(fcuParseResolution(undefined), { w: 3840, h: 2160 });
assert.deepEqual(fcuParseResolution('unknown'), { w: 3840, h: 2160 });
assert.deepEqual(fcuParseResolution('0x0'), { w: 3840, h: 2160 });

// fcuRotatedSize
assert.deepEqual(fcuRotatedSize(4000, 3000, 0), { w: 4000, h: 3000 });
assert.deepEqual(fcuRotatedSize(4000, 3000, 1), { w: 3000, h: 4000 });
assert.deepEqual(fcuRotatedSize(4000, 3000, 2), { w: 4000, h: 3000 });
assert.deepEqual(fcuRotatedSize(4000, 3000, 3), { w: 3000, h: 4000 });

// Cover / contain: a 4000×3000 photo on a 1920×1080 panel.
close(fcuCoverScale(1920, 1080, 4000, 3000), 0.48, 'fill scale');
close(fcuContainScale(1920, 1080, 4000, 3000), 0.36, 'fit scale');
// Rotated a quarter turn it is 3000×4000, so the width decides the fill.
const r = fcuRotatedSize(4000, 3000, 1);
close(fcuCoverScale(1920, 1080, r.w, r.h), 0.64, 'rotated fill scale');
close(fcuContainScale(1920, 1080, r.w, r.h), 0.27, 'rotated fit scale');

// fcuClampCentre: image wider than the frame (1920 at fill = 4000*0.48)...
// 4000*0.48 = 1920 on a 1920 frame: exactly flush, so centre is pinned.
close(fcuClampCentre(500, 1920, 1920), 960, 'flush image cannot move');
// ...zoomed to 3840 wide, the centre may move ±960 around the frame centre.
close(fcuClampCentre(0, 3840, 1920), 0, 'left edge flush with frame');
close(fcuClampCentre(-100, 3840, 1920), 0, 'no gap on the left');
close(fcuClampCentre(5000, 3840, 1920), 1920, 'no gap on the right');
close(fcuClampCentre(1200, 3840, 1920), 1200, 'inside the range is untouched');
// Smaller than the frame (fit with borders): it stays fully inside.
close(fcuClampCentre(0, 1000, 1920), 500, 'small image held inside on the left');
close(fcuClampCentre(1920, 1000, 1920), 1420, 'small image held inside on the right');

// fcuZoomAbout: 2× about the top-left corner moves the centre away from it.
assert.deepEqual(fcuZoomAbout(960, 540, 0.48, 0.96, 0, 0), { cx: 1920, cy: 1080 });
// About the centre itself, the centre does not move.
assert.deepEqual(fcuZoomAbout(960, 540, 0.48, 0.96, 960, 540), { cx: 960, cy: 540 });
// The point under the cursor stays put: image pixel at (200, 100) before and after.
{
  const px = 200, py = 100, s = 0.5, ns = 1.25, cx = 700, cy = 400;
  const { cx: ncx, cy: ncy } = fcuZoomAbout(cx, cy, s, ns, px, py);
  close((px - cx) / s, (px - ncx) / ns, 'x under the cursor is fixed');
  close((py - cy) / s, (py - ncy) / ns, 'y under the cursor is fixed');
}

// Slider: ends map to the range ends and it round-trips.
const lo = 0.36, hi = 0.48 * 6;
close(fcuSliderToScale(0, lo, hi), lo, 'slider 0 is fit');
close(fcuSliderToScale(1000, lo, hi), hi, 'slider 1000 is 6× fill');
for (const v of [0, 1, 137, 500, 999, 1000]) {
  assert.equal(fcuScaleToSlider(fcuSliderToScale(v, lo, hi), lo, hi), v, `round-trip ${v}`);
}
// Fill sits between the ends; out-of-range scales are clamped to the slider.
const fillPos = fcuScaleToSlider(0.48, lo, hi);
assert.ok(fillPos > 0 && fillPos < 1000, 'fill is inside the slider');
assert.equal(fcuScaleToSlider(lo * 0.999999, lo, hi), 0, 'just below fit clamps to 0');
assert.equal(fcuScaleToSlider(hi * 2, lo, hi), 1000, 'above max clamps to 1000');

// fcuCroppedName
assert.equal(fcuCroppedName('IMG_1234.HEIC'), 'IMG_1234_cropped.jpg');
assert.equal(fcuCroppedName('holiday.photo.jpg'), 'holiday.photo_cropped.jpg');
assert.equal(fcuCroppedName('.jpg'), 'frame_cropped.jpg');
assert.equal(fcuCroppedName(''), 'frame_cropped.jpg');

console.log('frame_crop_helpers: ok');
