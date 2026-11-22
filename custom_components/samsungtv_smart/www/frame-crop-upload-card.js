/*
 * frame-crop-upload-card — crop, pan, zoom and rotate a picture to the Frame's
 * screen shape in the browser, then push it through ha-samsungtv-smart's
 * authenticated /api/samsungtv_smart/art_upload endpoint.
 *
 * type: custom:frame-crop-upload-card
 * entity: media_player.samsung_frame    # required
 * title: Upload to The Frame            # optional
 * matte: none                           # optional; Frame matte id, e.g. shadowbox_polar
 */
const FCU_VERSION = "0.2.0";

// <fcu-pure-helpers> — pure, no DOM/hass; unit-tested in
// tests/js/frame_crop_helpers.test.mjs by extracting this region.

// "3840x2160" (either order) -> landscape { w, h }; 4K when missing or bad.
function fcuParseResolution(value) {
  const [a, b] = String(value || "").split("x").map(Number);
  if (!(a > 0) || !(b > 0)) return { w: 3840, h: 2160 };
  return { w: Math.max(a, b), h: Math.min(a, b) };
}

// Image size after rotating by rot quarter turns.
function fcuRotatedSize(w, h, rot) {
  return rot % 2 ? { w: h, h: w } : { w, h };
}

// Scale at which the image covers the W×H frame (crops the edges).
function fcuCoverScale(W, H, w, h) {
  return Math.max(W / w, H / h);
}

// Scale at which the whole image fits inside the W×H frame (borders).
function fcuContainScale(W, H, w, h) {
  return Math.min(W / w, H / h);
}

// Clamp the image centre on one axis so no gap opens at an edge while the
// image is larger than the frame, and the image stays inside it when smaller.
function fcuClampCentre(c, imgSize, frame) {
  const d = Math.abs(imgSize - frame) / 2;
  return Math.min(frame / 2 + d, Math.max(frame / 2 - d, c));
}

// New centre after zooming from scale s to newS about the point (px, py).
function fcuZoomAbout(cx, cy, s, newS, px, py) {
  const k = newS / s;
  return { cx: px + (cx - px) * k, cy: py + (cy - py) * k };
}

// The zoom slider (0..1000) is logarithmic between minScale and maxScale.
function fcuSliderToScale(v, minScale, maxScale) {
  return minScale * Math.pow(maxScale / minScale, v / 1000);
}
function fcuScaleToSlider(s, minScale, maxScale) {
  const v = Math.round((1000 * Math.log(s / minScale)) / Math.log(maxScale / minScale));
  return Math.min(1000, Math.max(0, v));
}

// "IMG_1234.HEIC" -> "IMG_1234_cropped.jpg"
function fcuCroppedName(name) {
  return (String(name || "").replace(/\.[^.]+$/, "") || "frame") + "_cropped.jpg";
}
// </fcu-pure-helpers>

class FrameCropUploadCard extends HTMLElement {
  static getStubConfig(hass) {
    const tv = Object.keys(hass.states).find(
      (e) => e.startsWith("media_player.") && hass.states[e].attributes.art_mode_status !== undefined
    );
    return { entity: tv || "media_player.samsung_frame" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("Set 'entity' to your Frame's media_player");
    this._config = config;
    if (this._root) this._root.querySelector(".title").textContent = config.title || "Upload to The Frame";
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) this._build();
    this._syncFromHass();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  /* ---------- DOM ---------- */
  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
<style>
  ha-card { padding: 16px; display: block; }
  .title { font-size: 1.2em; font-weight: 500; margin-bottom: 12px; }
  .stage { position: relative; margin-left: auto; margin-right: auto; background: repeating-conic-gradient(var(--secondary-background-color) 0 25%, var(--card-background-color) 0 50%) 0 0/16px 16px;
           border-radius: 8px; overflow: hidden; touch-action: none; cursor: grab; user-select: none; }
  .stage.drag { cursor: grabbing; }
  canvas { display: block; width: 100%; height: 100%; }
  .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
           color: var(--secondary-text-color); text-align: center; padding: 16px; pointer-events: none; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
  .row label { display: flex; align-items: center; gap: 6px; font-size: 0.95em; }
  .grow { flex: 1; min-width: 120px; }
  input[type=range] { width: 100%; }
  button, select { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--divider-color);
                   background: var(--secondary-background-color); color: var(--primary-text-color); cursor: pointer; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  button:disabled { opacity: 0.5; cursor: default; }
  input[type=color] { width: 40px; height: 36px; border: none; background: none; padding: 0; }
  .msg { margin-top: 10px; font-size: 0.95em; color: var(--secondary-text-color); }
  .msg.err { color: var(--error-color); }
  .hint { font-size: 0.85em; color: var(--secondary-text-color); margin-top: 6px; }
</style>
<ha-card>
  <div class="title">${this._config.title || "Upload to The Frame"}</div>
  <div class="row" style="margin-top:0">
    <button id="pick">Choose picture</button>
    <input id="file" type="file" accept="image/*" hidden>
    <span class="hint" id="target"></span>
  </div>
  <div class="stage" id="stage" style="margin-top:12px">
    <canvas id="view"></canvas>
    <div class="empty" id="empty">Choose a picture, then drag to move it and pinch or use the slider to zoom.</div>
  </div>
  <div class="row">
    <span>Zoom</span><div class="grow"><input id="zoom" type="range" min="0" max="1000" value="0"></div>
  </div>
  <div class="row">
    <button id="fill" title="Fill the screen, cropping the edges">Fill</button>
    <button id="fit" title="Show the whole picture, with borders">Fit</button>
    <button id="rot" title="Rotate 90°">⟳ Rotate</button>
    <button id="orient" title="Switch between landscape and portrait">Landscape</button>
    <label title="Colour of any borders when the picture doesn't fill the screen">Border <input id="bg" type="color" value="#000000"></label>
  </div>
  <div class="row">
    <label><input id="show" type="checkbox" checked> Show on the Frame after upload</label>
  </div>
  <div class="row">
    <button id="upload" class="primary" disabled>Upload</button>
    <button id="orig" hidden>Upload original instead</button>
  </div>
  <div class="msg" id="msg"></div>
</ha-card>`;
    const $ = (id) => this._root.getElementById(id);
    this._el = { card: this._root.querySelector("ha-card"), stage: $("stage"), view: $("view"), empty: $("empty"), zoom: $("zoom"), file: $("file"),
      upload: $("upload"), orig: $("orig"), msg: $("msg"), target: $("target"),
      show: $("show"), bg: $("bg"), orient: $("orient") };

    this._img = null; this._file = null; this._rot = 0; this._s = 1; this._cx = 0; this._cy = 0;
    this._portrait = null; this._pointers = new Map();

    $("pick").onclick = () => this._el.file.click();
    this._el.file.onchange = (e) => e.target.files[0] && this._load(e.target.files[0]);
    $("fill").onclick = () => this._reset("fill");
    $("fit").onclick = () => this._reset("fit");
    $("rot").onclick = () => { if (!this._img) return; this._rot = (this._rot + 1) % 4; this._reset("fill"); };
    this._el.orient.onclick = () => { this._portrait = !this._isPortrait(); this._layout(); this._reset("fill"); };
    this._el.bg.oninput = () => this._draw();
    this._el.zoom.oninput = () => this._zoomTo(this._sliderToScale(+this._el.zoom.value));
    this._el.upload.onclick = () => this._upload(false);
    this._el.orig.onclick = () => this._upload(true);

    const st = this._el.stage;
    st.addEventListener("pointerdown", (e) => this._down(e));
    st.addEventListener("pointermove", (e) => this._move(e));
    ["pointerup", "pointercancel", "pointerleave"].forEach((t) => st.addEventListener(t, (e) => this._up(e)));
    st.addEventListener("wheel", (e) => {
      if (!this._img) return;
      e.preventDefault();
      const p = this._toOut(e);
      this._zoomTo(this._s * Math.exp(-e.deltaY * 0.0015), p.x, p.y);
    }, { passive: false });

    this._ro = new ResizeObserver(() => this._layout());
    this._ro.observe(this._el.card);
  }

  _syncFromHass() {
    const tv = this._hass.states[this._config.entity];
    const res = fcuParseResolution(tv && tv.attributes.screen_resolution);
    if (!this._res || this._res.w !== res.w || this._res.h !== res.h) { this._res = res; this._layout(); }
  }

  /* ---------- geometry ---------- */
  _isPortrait() {
    if (this._portrait !== null) return this._portrait;
    const o = this._hass && this._hass.states[`sensor.${this._config.entity.split(".")[1]}_art_orientation`];
    return !!o && o.state === "portrait";
  }
  _out() { return this._isPortrait() ? { W: this._res.h, H: this._res.w } : { W: this._res.w, H: this._res.h }; }
  _eff() { return fcuRotatedSize(this._img.width, this._img.height, this._rot); }
  _cover() { const { W, H } = this._out(), e = this._eff(); return fcuCoverScale(W, H, e.w, e.h); }
  _contain() { const { W, H } = this._out(), e = this._eff(); return fcuContainScale(W, H, e.w, e.h); }
  _maxScale() { return this._cover() * 6; }
  _sliderToScale(v) { return fcuSliderToScale(v, this._contain(), this._maxScale()); }
  _scaleToSlider(s) { return fcuScaleToSlider(s, this._contain(), this._maxScale()); }

  _layout() {
    if (!this._res || !this._el) return;
    const { W, H } = this._out();
    const avail = this._el.card.clientWidth - 32 || 300;
    const w = Math.min(avail, (window.innerHeight * 0.6 * W) / H);
    const h = (w * H) / W;
    this._el.stage.style.width = `${w}px`;
    this._el.stage.style.height = `${h}px`;
    const dpr = window.devicePixelRatio || 1;
    this._el.view.width = Math.round(w * dpr);
    this._el.view.height = Math.round(h * dpr);
    this._el.target.textContent = `Screen: ${W}×${H}`;
    this._el.orient.textContent = this._isPortrait() ? "Portrait" : "Landscape";
    this._draw();
  }

  _clamp() {
    const { W, H } = this._out(), e = this._eff();
    this._cx = fcuClampCentre(this._cx, e.w * this._s, W);
    this._cy = fcuClampCentre(this._cy, e.h * this._s, H);
  }

  _reset(mode) {
    if (!this._img) return;
    const { W, H } = this._out();
    this._s = mode === "fit" ? this._contain() : this._cover();
    this._cx = W / 2; this._cy = H / 2;
    this._el.zoom.value = this._scaleToSlider(this._s);
    this._draw();
  }

  _zoomTo(s, px, py) {
    if (!this._img) return;
    const { W, H } = this._out();
    s = Math.min(this._maxScale(), Math.max(this._contain(), s));
    if (px === undefined) { px = W / 2; py = H / 2; }
    ({ cx: this._cx, cy: this._cy } = fcuZoomAbout(this._cx, this._cy, this._s, s, px, py));
    this._s = s;
    this._clamp();
    this._el.zoom.value = this._scaleToSlider(s);
    this._draw();
  }

  _render(ctx, W, H) {
    ctx.save();
    ctx.fillStyle = this._el.bg.value;
    ctx.fillRect(0, 0, W, H);
    ctx.translate(this._cx, this._cy);
    ctx.rotate((this._rot * Math.PI) / 2);
    ctx.scale(this._s, this._s);
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this._img, -this._img.width / 2, -this._img.height / 2);
    ctx.restore();
  }

  _draw() {
    const c = this._el.view, ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    if (!this._img || !this._res) return;
    const { W, H } = this._out();
    ctx.save();
    ctx.scale(c.width / W, c.height / H);
    this._render(ctx, W, H);
    ctx.restore();
  }

  /* ---------- pointer handling ---------- */
  _toOut(e) {
    const r = this._el.stage.getBoundingClientRect(), { W, H } = this._out();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  }
  _down(e) {
    if (!this._img) return;
    this._el.stage.setPointerCapture(e.pointerId);
    this._pointers.set(e.pointerId, this._toOut(e));
    this._el.stage.classList.add("drag");
    this._pinch = null;
  }
  _move(e) {
    if (!this._pointers.has(e.pointerId)) return;
    const prev = this._pointers.get(e.pointerId), cur = this._toOut(e);
    this._pointers.set(e.pointerId, cur);
    if (this._pointers.size === 1) {
      this._cx += cur.x - prev.x; this._cy += cur.y - prev.y;
      this._clamp(); this._draw();
    } else if (this._pointers.size === 2) {
      const [a, b] = [...this._pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y), mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      if (this._pinch) {
        this._cx += mid.x - this._pinch.mid.x; this._cy += mid.y - this._pinch.mid.y;
        this._zoomTo(this._s * (dist / this._pinch.dist), mid.x, mid.y);
      }
      this._pinch = { dist, mid };
    }
  }
  _up(e) {
    this._pointers.delete(e.pointerId);
    this._pinch = null;
    if (!this._pointers.size) this._el.stage.classList.remove("drag");
  }

  /* ---------- load & upload ---------- */
  async _load(file) {
    this._file = file; this._img = null; this._rot = 0;
    this._el.orig.hidden = true;
    this._setMsg("");
    try {
      const url = URL.createObjectURL(file);
      const img = new Image();
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
      this._img = img;
      this._el.empty.hidden = true;
      this._el.upload.disabled = false;
      this._el.orig.hidden = false;
      this._reset("fill");
    } catch (_) {
      this._el.empty.hidden = false;
      this._el.empty.textContent = "This browser can't preview this picture (e.g. HEIC), so it can't be cropped here.";
      this._el.upload.disabled = true;
      this._el.orig.hidden = false;
      this._setMsg("You can still upload it as-is; the Frame integration will fit it to the screen.");
    }
    this._el.file.value = "";
  }

  _matteId() { return this._config.matte || "none"; }

  async _upload(original) {
    if (this._busy || !this._file) return;
    this._busy = true;
    this._el.upload.disabled = this._el.orig.disabled = true;
    try {
      let blob = this._file, name = this._file.name;
      if (!original) {
        this._setMsg("Preparing picture…");
        const { W, H } = this._out();
        const c = document.createElement("canvas");
        c.width = W; c.height = H;
        this._render(c.getContext("2d"), W, H);
        blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.95));
        name = fcuCroppedName(this._file.name);
      }
      this._setMsg("Uploading to the Frame… this can take a little while.");
      const form = new FormData();
      form.append("entity_id", this._config.entity);
      form.append("matte_id", this._matteId());
      form.append("file", blob, name);
      const url = "/api/samsungtv_smart/art_upload";
      const resp = this._hass.fetchWithAuth
        ? await this._hass.fetchWithAuth(url, { method: "POST", body: form })
        : await fetch(url, { method: "POST", body: form,
            headers: { Authorization: `Bearer ${this._hass.auth.data.access_token}` } });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.message || `HTTP ${resp.status}`);
      let done = `Uploaded ✓ (${data.content_id || "done"})`;
      if (this._el.show.checked && data.content_id) {
        try {
          await this._hass.callService("samsungtv_smart", "art_select_image",
            { content_id: data.content_id, show: true }, { entity_id: this._config.entity });
          done += " and now showing on the Frame";
        } catch (err) { done += `, but couldn't show it: ${err.message}`; }
      }
      this._setMsg(done);
    } catch (err) {
      this._setMsg(`Upload failed: ${err.message}`, true);
    } finally {
      this._busy = false;
      this._el.upload.disabled = !this._img;
      this._el.orig.disabled = false;
    }
  }

  _setMsg(t, err = false) { this._el.msg.textContent = t; this._el.msg.classList.toggle("err", err); }
}

if (!customElements.get("frame-crop-upload-card")) {
  customElements.define("frame-crop-upload-card", FrameCropUploadCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "frame-crop-upload-card", name: "Frame crop & upload",
    description: "Crop, pan and zoom a picture to the Frame's screen, then upload it", preview: false });
  console.info(`%c FRAME-CROP-UPLOAD-CARD %c ${FCU_VERSION} `, "background:#03a9f4;color:#fff", "");
}
