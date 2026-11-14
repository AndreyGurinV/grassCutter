import "@fontsource/syne/700.css";
import "@fontsource/syne/800.css";
import "@fontsource/outfit/400.css";
import "@fontsource/outfit/600.css";
import "@fontsource/outfit/700.css";
import "./styles.css";
import { api, type CartStatus } from "./api";

const WHEEL_NAMES = ["FL", "FR", "RL", "RR"] as const;

type AppState = {
  status: CartStatus | null;
  busy: boolean;
  error: string;
  connected: boolean;
  v: number;
  w: number;
};

const state: AppState = {
  status: null,
  busy: false,
  error: "",
  connected: false,
  v: 0,
  w: 0,
};

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) {
  throw new Error("#app missing");
}

app.innerHTML = `
  <div class="shell">
    <header class="hero">
      <h1 class="brand">grass<span>Cutter</span></h1>
      <p class="tagline">Управление тележкой: задайте скорость стиком — она держится до новой команды или STOP.</p>
    </header>

    <div class="layout">
      <section class="panel controls">
        <h2>Drive</h2>
        <div class="stick-wrap">
          <div class="stick" id="stick" aria-label="Drive stick">
            <div class="knob" id="knob"></div>
          </div>
          <div class="drive-readout">
            <div><span>Forward v</span><strong id="vVal">0.00</strong></div>
            <div><span>Turn w</span><strong id="wVal">0.00</strong></div>
          </div>
        </div>
        <div class="row">
          <button type="button" class="danger" id="btnStop">STOP</button>
          <button type="button" class="primary" id="btnArm">Arm</button>
          <button type="button" id="btnDisarm">Disarm</button>
        </div>
        <p class="toast" id="toast" role="status"></p>
      </section>

      <section class="panel">
        <h2>Status</h2>
        <div class="status-grid">
          <div class="badges">
            <span class="badge" id="badgeConn"><i class="dot"></i> connection</span>
            <span class="badge" id="badgeArmed"><i class="dot"></i> armed</span>
            <span class="badge" id="badgeMock"><i class="dot"></i> mock</span>
          </div>
          <div class="wheels" id="wheels"></div>
          <p class="meta" id="meta">Ожидание связи с сервером…</p>
        </div>
      </section>
    </div>
  </div>
`;

const el = {
  stick: must("#stick"),
  knob: must("#knob"),
  vVal: must("#vVal"),
  wVal: must("#wVal"),
  btnStop: must<HTMLButtonElement>("#btnStop"),
  btnArm: must<HTMLButtonElement>("#btnArm"),
  btnDisarm: must<HTMLButtonElement>("#btnDisarm"),
  toast: must("#toast"),
  badgeConn: must("#badgeConn"),
  badgeArmed: must("#badgeArmed"),
  badgeMock: must("#badgeMock"),
  wheels: must("#wheels"),
  meta: must("#meta"),
};

el.wheels.innerHTML = WHEEL_NAMES.map(
  (name, i) => `
  <div class="wheel" data-i="${i}">
    <strong>${name}</strong>
    <div class="meta" data-speed>0.00</div>
    <div class="meter"><i data-bar></i></div>
  </div>`,
).join("");

function must<T extends HTMLElement = HTMLElement>(sel: string): T {
  const node = document.querySelector<T>(sel);
  if (!node) throw new Error(`missing ${sel}`);
  return node;
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function setBusy(busy: boolean): void {
  state.busy = busy;
  el.btnStop.disabled = busy;
  el.btnArm.disabled = busy;
  el.btnDisarm.disabled = busy;
}

function setError(msg: string): void {
  state.error = msg;
  el.toast.textContent = msg;
  el.toast.classList.toggle("error", Boolean(msg));
}

function renderStatus(): void {
  const s = state.status;
  el.badgeConn.classList.toggle("on", state.connected);
  el.badgeArmed.classList.toggle("on", Boolean(s?.armed));
  el.badgeMock.classList.toggle("warn", Boolean(s?.mock));

  el.wheels.querySelectorAll<HTMLElement>(".wheel").forEach((node) => {
    const i = Number(node.dataset.i);
    const speed = s?.speeds[i] ?? 0;
    const speedEl = node.querySelector<HTMLElement>("[data-speed]");
    const bar = node.querySelector<HTMLElement>("[data-bar]");
    if (speedEl) speedEl.textContent = `${speed.toFixed(2)}${s?.reverse[i] ? " R" : ""}`;
    if (bar) bar.style.width = `${Math.abs(speed) * 100}%`;
  });

  if (!s) {
    el.meta.textContent = "Нет данных статуса";
    return;
  }
  const volts = s.dac_volts.map((v) => v.toFixed(2)).join(" / ");
  el.meta.textContent = `DAC V: ${volts} · ESTOP_EN: ${s.estop_en ? "HIGH" : "LOW"}`;
}

function renderDrive(): void {
  el.vVal.textContent = state.v.toFixed(2);
  el.wVal.textContent = state.w.toFixed(2);
  const max = el.stick.clientWidth / 2 - el.knob.clientWidth / 2;
  el.knob.style.transform = `translate(calc(-50% + ${state.w * max}px), calc(-50% + ${-state.v * max}px))`;
}

async function withBusy<T>(fn: () => Promise<T>): Promise<T | null> {
  if (state.busy) return null;
  setBusy(true);
  setError("");
  try {
    const res = await fn();
    state.connected = true;
    return res;
  } catch (err) {
    state.connected = false;
    setError(err instanceof Error ? err.message : String(err));
    return null;
  } finally {
    setBusy(false);
    renderStatus();
  }
}

async function refreshStatus(): Promise<void> {
  try {
    const res = await api.status();
    state.status = res.status ?? null;
    state.connected = true;
    if (!state.error) setError("");
  } catch (err) {
    state.connected = false;
    if (!state.busy) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  renderStatus();
}

async function sendStop(): Promise<void> {
  state.v = 0;
  state.w = 0;
  renderDrive();
  el.knob.classList.remove("active");
  const res = await withBusy(() => api.stop());
  if (res?.status) state.status = res.status;
  renderStatus();
}

function setupStick(): void {
  let dragging = false;
  let lastSent = 0;
  let pending: { v: number; w: number } | null = null;
  let sending = false;

  const updateFromPoint = (clientX: number, clientY: number): void => {
    const rect = el.stick.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const radius = rect.width / 2;
    let dx = (clientX - cx) / radius;
    let dy = (clientY - cy) / radius;
    const mag = Math.hypot(dx, dy);
    if (mag > 1) {
      dx /= mag;
      dy /= mag;
    }
    // deadzone
    if (Math.hypot(dx, dy) < 0.06) {
      dx = 0;
      dy = 0;
    }
    state.w = round2(clamp(dx, -1, 1));
    state.v = round2(clamp(-dy, -1, 1));
    renderDrive();
    queueDrive(state.v, state.w);
  };

  const queueDrive = (v: number, w: number): void => {
    pending = { v, w };
    const now = performance.now();
    if (sending || now - lastSent < 120) return;
    void flushDrive();
  };

  const flushDrive = async (): Promise<void> => {
    if (!pending || sending) return;
    const next = pending;
    pending = null;
    sending = true;
    lastSent = performance.now();
    try {
      const res = await api.drive(next.v, next.w);
      state.status = res.status ?? state.status;
      state.connected = true;
      setError("");
      renderStatus();
    } catch (err) {
      state.connected = false;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      sending = false;
      if (pending) void flushDrive();
    }
  };

  const onDown = (e: PointerEvent): void => {
    dragging = true;
    el.knob.classList.add("active");
    el.stick.setPointerCapture(e.pointerId);
    updateFromPoint(e.clientX, e.clientY);
  };

  const onMove = (e: PointerEvent): void => {
    if (!dragging) return;
    updateFromPoint(e.clientX, e.clientY);
  };

  const onUp = (e: PointerEvent): void => {
    if (!dragging) return;
    dragging = false;
    el.knob.classList.remove("active");
    try {
      el.stick.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    // Keep last commanded speed until STOP / new command.
  };

  el.stick.addEventListener("pointerdown", onDown);
  el.stick.addEventListener("pointermove", onMove);
  el.stick.addEventListener("pointerup", onUp);
  el.stick.addEventListener("pointercancel", onUp);
}

el.btnStop.addEventListener("click", () => {
  void sendStop();
});

el.btnArm.addEventListener("click", async () => {
  const res = await withBusy(() => api.arm());
  if (res?.status) state.status = res.status;
  renderStatus();
});

el.btnDisarm.addEventListener("click", async () => {
  state.v = 0;
  state.w = 0;
  renderDrive();
  const res = await withBusy(() => api.disarm());
  if (res?.status) state.status = res.status;
  renderStatus();
});

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" || e.key === "Escape") {
    e.preventDefault();
    void sendStop();
  }
});

setupStick();
renderDrive();
renderStatus();
void refreshStatus();
window.setInterval(() => {
  if (!state.busy) void refreshStatus();
}, 1500);
