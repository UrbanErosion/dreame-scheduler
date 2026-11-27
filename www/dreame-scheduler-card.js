/* Dreame Scheduler — custom Lovelace card.
 *
 * A self-contained web component (no build step) that shows the scheduler's
 * week status + quick controls. Registers itself in the card picker so it can
 * be added from the HA dashboard GUI ("Add card" → "Dreame Scheduler").
 *
 * Config: { type: "custom:dreame-scheduler-card", entity: sensor.<vacuum>_scheduler_status }
 * The sibling entities (buttons/switch/rooms-cleaned) are derived from that
 * status sensor's id, so a single entity is all that's needed — portable to
 * any user's vacuum.
 *
 * Deploy: copy to /config/www/ and add /local/dreame-scheduler-card.js as a
 * Lovelace resource (type: module). The add-on's Dashboard tab can do this.
 */

const VERSION = "1.2.0";
const RETURN_HOME = 16; // Home Assistant VacuumEntityFeature.RETURN_HOME.

class DreameSchedulerCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    }
    if (config.vacuum_entity && !/^vacuum\.[a-z0-9_]+$/.test(config.vacuum_entity)) {
      throw new Error("Set 'vacuum_entity' to the vacuum that this scheduler controls.");
    }
    this._config = config;
    this._built = false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 6; }

  // Sibling entity ids derived from the status sensor's object_id prefix.
  _ids() {
    const status = this._config.entity;                 // sensor.<base>_status
    const base = status.replace(/^sensor\./, "").replace(/_status$/, "");
    return {
      status,
      rooms_cleaned: `sensor.${base}_rooms_cleaned_this_week`,
      enabled: `switch.${base}_scheduler_enabled`,
      run_today: `button.${base}_run_today_s_schedule_now`,
      clean_now: `button.${base}_clean_now`,
      clean_quiet: `button.${base}_clean_now_quiet`,
      reset_week: `button.${base}_reset_week_counters`,
    };
  }

  _canDock(robot) {
    return robot && ["cleaning", "paused"].includes(robot.state) &&
      (robot.attributes?.supported_features & RETURN_HOME) !== 0;
  }

  _build() {
    this.innerHTML = `
      <ha-card><style>
        .dsc { --accent:var(--primary-color,#167c8a); --line:var(--divider-color,rgba(128,128,128,.35));
          --muted:var(--secondary-text-color,#66717b); --surface:var(--secondary-background-color,rgba(128,128,128,.08));
          padding:16px; color:var(--primary-text-color,inherit); font-family:var(--primary-font-family,Roboto,Arial,sans-serif); font-size:14px; line-height:1.45; overflow-wrap:anywhere; }
        .dsc [hidden] { display:none !important; }
        .dsc-header,.dsc-summary { display:grid; align-items:center; gap:12px; }
        .dsc-header { grid-template-columns:minmax(0,1fr) auto; }
        .dsc-title { margin:0; min-width:0; font-size:1.15rem; line-height:1.25; font-weight:650; }
        .dsc-switch { flex:none; display:flex; gap:6px; align-items:center; min-height:44px; cursor:pointer; color:var(--muted); }
        .dsc-switch input { appearance:none; -webkit-appearance:none; box-sizing:border-box; position:relative; flex:none;
          width:32px; height:19px; margin:0 4px 0 0; border:1px solid var(--muted); background:var(--surface); border-radius:12px; cursor:pointer; }
        .dsc-switch input::before { content:""; position:absolute; left:3px; top:3px; width:11px; height:11px; border-radius:50%; background:var(--muted); }
        .dsc-switch input:checked { border-color:var(--accent); background:var(--accent); }
        .dsc-switch input:checked::before { left:16px; background:var(--card-background-color,#fff); }
        .dsc-switch input:indeterminate::before { left:10px; }
        .dsc-switch input:disabled { opacity:.65; cursor:default; }
        @media (forced-colors:active) { .dsc-switch input { appearance:auto; -webkit-appearance:auto; } .dsc-switch input::before { display:none; } }
        .dsc-status { margin:0 0 12px; }
        .dsc-state { font-weight:600; }
        .dsc-reason,.dsc-meta { color:var(--muted); }
        .dsc-state:not(:empty) + .dsc-reason:not(:empty)::before { content:" · "; }
        .dsc-coverage { border:1px solid var(--line); border-radius:14px; overflow:hidden; }
        .dsc-summary { grid-template-columns:auto minmax(0,1fr) auto; padding:13px 14px; background:var(--surface); list-style:none; cursor:pointer; }
        .dsc-summary::-webkit-details-marker { display:none; }
        .dsc-dial { --angle:0deg; position:relative; width:46px; height:46px; flex:none; border-radius:50%;
          background:conic-gradient(from -90deg,var(--accent) var(--angle),var(--line) 0); }
        .dsc-dial::before { content:""; position:absolute; top:4px; right:4px; bottom:4px; left:4px; border-radius:50%; background:var(--card-background-color,var(--ha-card-background,#fff)); }
        .dsc-dial::after { content:""; position:absolute; top:11px; right:11px; bottom:11px; left:11px; border:2px solid var(--muted); border-radius:50%;
          background:radial-gradient(circle at 50% 32%,var(--muted) 0 16%,transparent 18%); }
        .dsc-summary-text { flex:1; min-width:0; }
        .dsc-meta { font-size:.82rem; }
        .dsc-count { display:block; font-weight:650; font-size:1.05rem; }
        .dsc-chevron { width:8px; height:8px; margin-right:4px; flex:none; border:solid var(--muted); border-width:0 2px 2px 0; transform:rotate(45deg); }
        .dsc details[open] .dsc-chevron { transform:rotate(225deg); }
        .dsc-lists { border-top:1px solid var(--line); padding:2px 14px 12px; }
        .dsc-group { font-weight:600; margin:10px 0 4px; font-size:.88rem; }
        .dsc-rooms { margin:0 -4px -4px 0; padding:0; display:flex; flex-wrap:wrap; list-style:none; }
        .dsc-rooms li { box-sizing:border-box; margin:0 4px 4px 0; border:1px solid var(--line); border-radius:8px; padding:3px 8px; max-width:100%; }
        .dsc-blocked { font-size:.8rem; color:var(--muted); }
        .dsc-empty { color:var(--muted); margin:10px 0 0; }
        .dsc-last { margin:12px 0 0; }
        .dsc-last strong { font-size:.88rem; }
        .dsc-last-values { color:var(--muted); }
        .dsc-last-values::before { content:" · "; }
        .dsc-actions { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,120px),1fr)); gap:7px; margin-top:14px; }
        .dsc-btn { min-height:44px; padding:6px 10px; border-radius:9px; border:1px solid var(--line); background:transparent;
          font:inherit; font-size:.88rem; color:inherit; cursor:pointer; }
        .dsc-btn:disabled { color:var(--muted); opacity:.65; cursor:default; }
        .dsc-btn:not(:disabled):hover,.dsc-summary:hover { border-color:var(--accent); }
        .dsc-btn[data-action="run_today"],.dsc-btn[data-action="dock"] { font-weight:650; }
        .dsc-btn[data-action="run_today"],.dsc-btn[data-action="dock"] { border-color:var(--accent); background:var(--surface); }
        .dsc :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
        .dsc-summary:focus-visible { outline-offset:-3px; }
      </style><div class="dsc">
        <div class="dsc-header"><h2 class="dsc-title"></h2>
          <label class="dsc-switch"><input type="checkbox" data-en> Enabled</label></div>
        <p class="dsc-status"><span class="dsc-state"></span><span class="dsc-reason"></span></p>
        <details class="dsc-coverage"><summary class="dsc-summary">
          <span class="dsc-dial" aria-hidden="true"></span>
          <span class="dsc-summary-text"><span class="dsc-meta">Weekly room coverage</span><strong class="dsc-count"></strong></span>
          <span class="dsc-chevron" aria-hidden="true"></span>
        </summary><div class="dsc-lists"></div></details>
        <p class="dsc-last" hidden><strong>Last run</strong><span class="dsc-last-values"></span></p>
        <div class="dsc-actions">
          <button class="dsc-btn" data-action="dock" hidden>Return to base</button>
          <button class="dsc-btn" data-action="run_today">Run today’s plan</button>
          <button class="dsc-btn" data-action="clean_now">Clean pending</button>
          <button class="dsc-btn" data-action="clean_quiet">Clean pending quietly</button>
          <button class="dsc-btn" data-action="reset_week">Reset week</button>
        </div>
      </div></ha-card>`;
    this.querySelector(".dsc-actions").addEventListener("click", event => {
      const button = event.target.closest("button[data-action]");
      if (!button || button.disabled || button.hidden) return;
      const action = button.dataset.action;
      if (action === "dock") {
        const vacuum = this._config.vacuum_entity;
        if (this._canDock(this._hass.states[vacuum])) {
          this._hass.callService("vacuum", "return_to_base", { entity_id: vacuum });
        }
        return;
      }
      const id = this._ids()[action];
      if (action === "reset_week" && !window.confirm("Reset this scheduler’s room credits for the week?")) return;
      this._hass.callService("button", "press", { entity_id: id });
    });
    this.querySelector("[data-en]").addEventListener("change", () => {
      const id = this._ids().enabled;
      const st = this._hass.states[id];
      if (st && ["on","off"].includes(st.state)) {
        this._hass.callService("switch", st.state === "on" ? "turn_off" : "turn_on", { entity_id: id });
      }
    });
    this._roomMarkup = null;
    this._built = true;
  }

  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const ids = this._ids();
    const s = hass.states[ids.status];
    const robot = cfg.vacuum_entity && hass.states[cfg.vacuum_entity];
    const relevant = [...Object.values(ids).map(id => hass.states[id]), robot];
    if (this._built && relevant.every((state, i) => state === this._previous[i])) return;
    if (!this._built) this._build();
    this._previous = relevant;

    const online = Boolean(s) && !["unavailable", "unknown"].includes(s.state);
    const a = online ? (s.attributes || {}) : {};
    const title = cfg.title || s?.attributes?.friendly_name?.replace(/ Status$/, "") || "Vacuum scheduler";
    this.querySelector(".dsc-title").textContent = title;
    const label = s ? _cap(s.state) : "Scheduler unavailable";
    const reason = s ? String(a.reason || "") : ids.status;
    const startsWithState = reason.toLowerCase().startsWith(label.toLowerCase() + " ");
    this.querySelector(".dsc-state").textContent = startsWithState ? "" : label;
    this.querySelector(".dsc-reason").textContent = reason;

    const rc = hass.states[ids.rooms_cleaned];
    const cleaned = Array.isArray(a.cleaned_rooms) ? a.cleaned_rooms : null;
    const rcReady = online && rc && /^\d+$/.test(rc.state);
    const pending = Array.isArray(a.pending_rooms) ? a.pending_rooms :
      rcReady && Array.isArray(rc.attributes?.pending_rooms) ? rc.attributes.pending_rooms : null;
    const done = cleaned ? cleaned.length : rcReady ? Number(rc.state) : null;
    const total = done !== null && pending ? done + pending.length :
      rcReady && Number.isSafeInteger(rc.attributes?.rooms_total) ? rc.attributes.rooms_total : null;
    const coverageKnown = done !== null && total !== null && total >= done;
    const count = coverageKnown ? (total ? `${done} of ${total} room${total === 1 ? "" : "s"} credited` : "No rooms scheduled") :
      "Room totals unavailable";
    this.querySelector(".dsc-count").textContent = count;
    this.querySelector(".dsc-dial").style.setProperty("--angle", `${coverageKnown && total ? done * 360 / total : 0}deg`);

    // Replace only the non-interactive lists. A live state update preserves
    // the native disclosure's expanded state, focus and scroll position.
    const misses = a.missed_rooms || a.unreachable || {};
    const group = (name, rooms, showMisses = false) => rooms?.length ? `<h3 class="dsc-group">${name}</h3><ul class="dsc-rooms">${
      rooms.map(room => {
        const blocked = showMisses && Object.prototype.hasOwnProperty.call(misses, room) && Number(misses[room]) > 0;
        return `<li>${_esc(room)}${blocked ? ` <span class="dsc-blocked">· blocked ×${_esc(misses[room])}</span>` : ""}</li>`;
      }).join("")}</ul>` : "";
    const otherMisses = Object.keys(misses).filter(room => !pending?.includes(room));
    const fallback = cleaned || pending ? "No room names reported." : a.week_summary || "Room details unavailable.";
    const roomMarkup = group("Credited", cleaned) + group("Pending", pending, true) + group("Blocked attempts", otherMisses, true) ||
      `<p class="dsc-empty">${_esc(fallback)}</p>`;
    if (roomMarkup !== this._roomMarkup) {
      this.querySelector(".dsc-lists").innerHTML = roomMarkup;
      this._roomMarkup = roomMarkup;
    }
    const last = a.last_run;
    const outcome = [];
    if (last?.interrupted === true) outcome.push("Interrupted");
    if (Array.isArray(last?.cleaned)) {
      const n = last.cleaned.length;
      outcome.push(`${n} room${n === 1 ? "" : "s"} credited`);
    }
    if (typeof last?.cleaned_area === "string" && last.cleaned_area) outcome.push(last.cleaned_area);
    const lastEl = this.querySelector(".dsc-last");
    lastEl.hidden = !outcome.length;
    lastEl.querySelector(".dsc-last-values").textContent = outcome.join(" · ");

    const sw = hass.states[ids.enabled];
    const toggle = this.querySelector("[data-en]");
    toggle.disabled = !online || !sw || !["on","off"].includes(sw.state);
    toggle.indeterminate = typeof a.enabled !== "boolean" && (!sw || !["on","off"].includes(sw.state));
    toggle.checked = sw && ["on","off"].includes(sw.state) ? sw.state === "on" : a.enabled === true;

    for (const button of this.querySelectorAll("button[data-action]")) {
      const action = button.dataset.action;
      if (action === "dock") {
        button.hidden = !this._canDock(robot);
      } else {
        const target = hass.states[ids[action]];
        // A button's state is "unknown" until its first press.
        button.disabled = !online || !target || target.state === "unavailable";
      }
    }
  }

  static getStubConfig(hass) {
    const found = Object.keys(hass.states || {}).find(
      id => id.startsWith("sensor.") && id.endsWith("_scheduler_status"));
    return { entity: found || "sensor.vacuum_scheduler_status" };
  }
}

customElements.define("dreame-scheduler-card", DreameSchedulerCard);

// Shared bits for the smaller companion cards (robot + presence). Both read
// everything from the one scheduler status sensor's attributes, so — like the
// main card — a single entity is all the config they need.
const _cap = (s) => (s ? String(s).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—");
// Escape freeform device/app text (room names, error strings, chip states) before it goes into
// innerHTML — these come from the Dreame app/device and could otherwise inject markup.
const _esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function _stubStatus(hass) {
  const found = Object.keys(hass.states || {}).find(
    (id) => id.startsWith("sensor.") && id.endsWith("_scheduler_status"));
  return { entity: found || "sensor.vacuum_scheduler_status" };
}
const _CARD_CSS = `
  .dsc2 { padding: 14px 16px 16px; }
  .dsc2-h { font-size:16px; font-weight:600; margin-bottom:10px; }
  .dsc2-top { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:6px; }
  .dsc2-state { font-size:15px; font-weight:600; }
  .dsc2-bat { font-size:14px; color:var(--secondary-text-color); white-space:nowrap; }
  .dsc2-row { font-size:14px; margin:4px 0; }
  .dsc2-err { color:var(--error-color,#e53935); }
  .dsc2-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .dsc2-chip { font-size:12px; padding:4px 9px; border-radius:999px; border:1px solid var(--divider-color,#3336); background:var(--secondary-background-color,#2226); }
  .dsc2-muted { color:var(--secondary-text-color); }`;

/* ---- Robot status card ---- */
class DreameRobotCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    this._config = config;
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 3; }
  static getStubConfig(hass) { return _stubStatus(hass); }
  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const s = hass.states[cfg.entity];
    if (!s) { this.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color,#c00)">Unknown entity: ${_esc(cfg.entity)}</div></ha-card>`; return; }
    const r = (s.attributes && s.attributes.robot) || {};
    const bat = (r.battery != null) ? Math.round(r.battery) + "%" : "—";
    const batIcon = (r.battery != null && r.battery < 20) ? "🪫" : "🔋";
    const errNorm = r.error ? String(r.error).toLowerCase().replace(/_/g, " ").trim() : "";
    const err = errNorm && !["none", "no error", "0", "ok"].includes(errNorm) ? r.error : null;
    const chip = (lbl, v) => v ? `<span class="dsc2-chip">${lbl} ${_esc(_cap(v))}</span>` : "";
    const chips = chip("🗑️", r.dust_bag) + chip("💧", r.clean_water) + chip("🪣", r.dirty_water);
    this.innerHTML = `<ha-card><style>${_CARD_CSS}</style><div class="dsc2">
      <div class="dsc2-h">🤖 ${_esc(cfg.title || "Robot status")}</div>
      <div class="dsc2-top"><span class="dsc2-state">🧭 ${_esc(_cap(r.status || r.vacuum_state))}</span><span class="dsc2-bat">${batIcon} ${bat}</span></div>
      ${r.current_room ? `<div class="dsc2-row">📍 In <b>${_esc(r.current_room)}</b></div>` : ""}
      ${err ? `<div class="dsc2-row dsc2-err">⚠️ ${_esc(_cap(err))}</div>` : ""}
      ${chips ? `<div class="dsc2-chips">${chips}</div>` : ""}
    </div></ha-card>`;
  }
}
customElements.define("dreame-robot-card", DreameRobotCard);

/* ---- Presence & next run card ---- */
class DreamePresenceCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set 'entity' to the scheduler status sensor (sensor.*_scheduler_status).");
    this._config = config;
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 2; }
  static getStubConfig(hass) { return _stubStatus(hass); }
  _render() {
    const hass = this._hass, cfg = this._config;
    if (!hass || !cfg) return;
    const s = hass.states[cfg.entity];
    if (!s) { this.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color,#c00)">Unknown entity: ${_esc(cfg.entity)}</div></ha-card>`; return; }
    const a = s.attributes || {};
    let presence;
    if (!a.presence_configured) presence = `<div class="dsc2-row dsc2-muted">No presence entities set — runs aren't presence-gated.</div>`;
    else presence = `<div class="dsc2-state">${a.presence_home === true ? "🏠 Someone's home" : a.presence_home === false ? "🚶 Everyone's out" : "❓ Presence unknown"}</div>`;
    const next = a.next_run_day
      ? `<div class="dsc2-row">⏰ Next run <b>${_esc(a.next_run_day)}</b> at <b>${_esc(a.next_run_time)}</b></div>`
      : `<div class="dsc2-row dsc2-muted">No upcoming scheduled run — enable rooms &amp; days.</div>`;
    this.innerHTML = `<ha-card><style>${_CARD_CSS}</style><div class="dsc2">
      <div class="dsc2-h">🏠 ${_esc(cfg.title || "Presence & next run")}</div>
      ${presence}
      ${next}
    </div></ha-card>`;
  }
}
customElements.define("dreame-presence-card", DreamePresenceCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "dreame-scheduler-card",
  name: "Dreame Scheduler",
  description: "Presence-aware vacuum room scheduler — week status + controls.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
}, {
  type: "dreame-robot-card",
  name: "Dreame Robot Status",
  description: "Live robot state — battery, what it's doing, error, tanks & bag.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
}, {
  type: "dreame-presence-card",
  name: "Dreame Presence & Next Run",
  description: "Who's home + when the scheduler next runs.",
  preview: true,
  documentationURL: "https://github.com/botts7/dreame-scheduler",
});
console.info(`%c DREAME-SCHEDULER-CARD %c ${VERSION} `, "color:#fff;background:#3b82f6;border-radius:3px 0 0 3px", "color:#3b82f6;background:#1a1d28;border-radius:0 3px 3px 0");
