/**
 * samsung-art-upload-card
 *
 * One-click artwork upload to a Samsung Frame TV: pick an image on any device
 * (phone, laptop) and it is pushed straight to the Frame — no folder sensor,
 * no pre-placed file. The file is POSTed to the integration's authenticated
 * endpoint (/api/samsungtv_smart/art_upload), which reuses the art_upload
 * service to display/refresh it.
 *
 * Config:
 *   type: custom:samsung-art-upload-card
 *   entity: media_player.samsung_frame   # optional; if omitted a picker is shown
 *   matte: shadowbox_polar               # optional default matte
 *   title: Upload to The Frame           # optional
 */

class SamsungArtUploadCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._file = null;
    this._busy = false;
    this._msg = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._render();
  }

  getCardSize() {
    return 3;
  }

  _frameEntities() {
    if (!this._hass) return [];
    // Frame TVs are the media_players that expose the art_mode_status attribute.
    return Object.keys(this._hass.states).filter(
      (e) =>
        e.startsWith("media_player.") &&
        "art_mode_status" in (this._hass.states[e].attributes || {})
    );
  }

  _selectedEntity() {
    if (this._config.entity) return this._config.entity;
    const sel = this.querySelector("#stv-entity");
    return sel ? sel.value : this._frameEntities()[0];
  }

  async _upload() {
    if (this._busy) return;
    const entity = this._selectedEntity();
    if (!entity) return this._setMsg("No Frame TV entity selected.", true);
    if (!this._file) return this._setMsg("Pick an image first.", true);

    this._busy = true;
    this._setMsg("Uploading…");
    this._sync();

    const form = new FormData();
    form.append("entity_id", entity);
    form.append("matte_id", this._config.matte || "shadowbox_polar");
    form.append("file", this._file, this._file.name);

    try {
      // Use fetchWithAuth so the frontend renews an expired access token before
      // the request. The stored hass.auth.data.access_token lives 30 min, so a
      // plain fetch with it failed with 401 on a page left open longer (#396).
      const url = "/api/samsungtv_smart/art_upload";
      const resp = this._hass.fetchWithAuth
        ? await this._hass.fetchWithAuth(url, { method: "POST", body: form })
        : await fetch(url, {
            method: "POST",
            headers: this._hass.auth?.data?.access_token
              ? { Authorization: `Bearer ${this._hass.auth.data.access_token}` }
              : {},
            body: form,
          });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.message || `HTTP ${resp.status}`);
      this._setMsg(`Uploaded ✓ (${data.content_id || "done"})`);
      this._file = null;
      const input = this.querySelector("#stv-file");
      if (input) input.value = "";
    } catch (err) {
      this._setMsg(`Failed: ${err.message}`, true);
    } finally {
      this._busy = false;
      this._sync();
    }
  }

  _setMsg(msg, error = false) {
    this._msg = msg;
    this._error = error;
  }

  _sync() {
    const btn = this.querySelector("#stv-btn");
    if (btn) {
      btn.disabled = this._busy || !this._file;
      btn.textContent = this._busy ? "Uploading…" : "Upload to Frame";
    }
    const msg = this.querySelector("#stv-msg");
    if (msg) {
      msg.textContent = this._msg;
      msg.style.color = this._error ? "var(--error-color)" : "var(--secondary-text-color)";
    }
  }

  _render() {
    if (!this._hass) return;
    this._built = true;
    const cfg = this._config || {};
    const entities = this._frameEntities();
    const picker =
      !cfg.entity && entities.length > 1
        ? `<select id="stv-entity" style="width:100%;padding:8px;margin-bottom:8px">
             ${entities
               .map(
                 (e) =>
                   `<option value="${e}">${
                     this._hass.states[e].attributes.friendly_name || e
                   }</option>`
               )
               .join("")}
           </select>`
        : "";

    this.innerHTML = `
      <ha-card header="${cfg.title || "Upload to The Frame"}">
        <div style="padding:16px;display:flex;flex-direction:column;gap:12px">
          ${picker}
          <input id="stv-file" type="file" accept="image/*,.heic,.heif" />
          <button id="stv-btn" disabled
            style="padding:10px;border:none;border-radius:10px;cursor:pointer;
                   background:var(--primary-color);color:var(--text-primary-color);
                   font-size:14px">Upload to Frame</button>
          <div id="stv-msg" style="font-size:13px;min-height:1em"></div>
        </div>
      </ha-card>`;

    this.querySelector("#stv-file").addEventListener("change", (ev) => {
      this._file = ev.target.files && ev.target.files[0];
      this._setMsg(this._file ? this._file.name : "");
      this._sync();
    });
    this.querySelector("#stv-btn").addEventListener("click", () => this._upload());
    this._sync();
  }
}

customElements.define("samsung-art-upload-card", SamsungArtUploadCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "samsung-art-upload-card",
  name: "Samsung Frame Art Upload",
  description: "Pick an image and push it straight to a Samsung Frame TV.",
});
