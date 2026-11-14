"use strict";

const get = (id) => document.getElementById(id);
const actions = [...document.querySelectorAll("button[data-action]")];
const pages = new Set(["overview", "mapping", "navigation", "maps", "devices"]);
const mapActions = new Set(["convert", "prepare", "load"]);
const labels = {running: "进行中", done: "已完成", failed: "未完成"};
let state = null;
let selected = null;
let listing = [];
let page = "overview";
let pending = false;
let pollBusy = false;
let plannerState = "等待三维连接";
let socketState = "未连接";
let navigationRun = null;
let lastJob = null;
let mapRequest = 0;
let online = false;

async function api(path, {body, csrf} = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers, body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(8000),
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "请求未完成。");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function details(element, pairs) {
  element.replaceChildren(...pairs.map(([label, value]) => {
    const entry = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "未知";
    entry.append(term, description);
    return entry;
  }));
}

function setPlanner(show) {
  const frame = get("planner");
  if (show && !frame.hasAttribute("src")) frame.src = "/planner/";
  if (!show && frame.hasAttribute("src")) {
    frame.removeAttribute("src");
    plannerState = "等待三维连接";
    socketState = "未连接";
  }
  frame.hidden = !show;
}

function buttons() {
  for (const button of actions) {
    const name = button.dataset.action;
    button.disabled = !online || pending || !state?.allowed.includes(name) ||
      (mapActions.has(name) && !selected) ||
      (name === "navigation-stop" && state?.motionGate?.canStop);
  }
}

function render() {
  for (const item of document.querySelectorAll(".page")) item.hidden = item.id !== page;
  for (const link of document.querySelectorAll("nav a")) {
    if (link.hash === "#" + page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (!state) {
    setPlanner(false);
    get("cloud-preview").removeAttribute("src");
    get("cloud-preview").hidden = true;
    get("signals").replaceChildren();
    buttons();
    return;
  }
  if (!online) {
    setPlanner(false);
    get("cloud-preview").removeAttribute("src");
    get("cloud-preview").hidden = true;
    get("signals").textContent = "已断开，设备状态未知";
    buttons();
    return;
  }
  const odin = state.odin === true ? "持续更新" : state.odin === false ? "暂未收到持续数据" : "未知";
  const current = state.currentMap || (state.selectedMap ? "待确认加载 · " + state.selectedMap : "未加载");
  const job = state.job;
  const jobLine = job ? (labels[job.status] + " · " + job.message) : "暂无";
  get("signals").replaceChildren(...[
    state.mode, "AGX " + state.agx, "Odin1 " + odin, "SDK " + state.bridge, "定位 " + state.localization,
  ].map((label) => Object.assign(document.createElement("span"), {textContent: label})));
  details(get("summary"), [
    ["当前地图", current], ["最近一次运行结果", jobLine],
    ["操作条件", !state.installed ? "负责人需完成安装" : !state.authorized ? "负责人需确认厂商资源和授权" :
      !state.cameraConfirmed ? "负责人需确认相机和现场部署" : "请按设备状态和页面提示操作"],
  ]);
  get("mapping-status").textContent = state.mapping.live ? "驱动运行中" + (state.mapping.saved ? "，本次 BIN 已确认保存。" : "，本次 BIN 尚未确认。") : state.mode === "建图待恢复" ? "建图配置待恢复，请联系负责人。" : "建图尚未启动。";
  if (state.mapping.output) get("mapping-status").textContent += " 输出目录 " + state.mapping.output;
  get("mapping-job").textContent = job?.action.startsWith("mapping-") ? jobLine : "";
  get("cloud-status").textContent = state.mapping.cloud === true
    ? "正在接收 Odin1 实时点云。"
    : state.mapping.cloud === false ? "暂时没有收到持续点云，请检查相机供电与连接。"
    : "实时点云状态未知。";
  const cloudFrame = get("cloud-preview");
  const showCloud = state.mapping.preview && page === "mapping";
  if (showCloud && !cloudFrame.hasAttribute("src")) cloudFrame.src = "/planner/?preview=odin";
  if (!showCloud) cloudFrame.removeAttribute("src");
  cloudFrame.hidden = !showCloud;
  if (navigationRun !== state.navigationRun) {
    setPlanner(false);
    navigationRun = state.navigationRun;
  }
  setPlanner(state.navigation.planner && (page === "navigation" || get("planner").hasAttribute("src")));
  if (get("planner").hasAttribute("src")) {
    get("planner").contentWindow?.postMessage({
      source: "odin3d-console", canPlan: state.navigation.canPlan && !pending,
    }, location.origin);
  }
  details(get("nav-status"), [
    ["当前地图", current], ["定位", state.localization], ["目标与路径", plannerState],
    ["导航任务", state.navigation.execution], ["机器狗是否运动", state.navigation.motion],
    ["最近操作", jobLine],
  ]);
  get("planner-hint").textContent = state.navigation.planner ? "" : "请在地图管理加载现场地图，等待三维规划服务就绪。";
  if (state.navigation.planner && !state.navigation.canPlan) {
    get("planner-hint").textContent = state.localization !== "持续更新"
      ? "定位尚未完成，暂时不能选点规划。请核对现场地图与 Odin1 数据。"
      : "当前暂停网页选点。请等待任务结束，或由负责人退出现场控制。";
  }
  const motion = state.motionGate;
  get("motion-status").textContent = motion.message + " SDK：" + (motion.sdk || "未知") +
    "；定位：" + (motion.localization || state.localization) + "；模式：" + (motion.mode || state.mode);
  get("motion-prepare").disabled = !motion.canPrepare || pending;
  get("motion-start").disabled = !motion.canStart || pending;
  get("motion-stop").disabled = !motion.canStop;
  details(get("device-status"), [
    ["控制台", state.address], ["Odin1 数据", odin === "暂未收到持续数据" ? "请检查相机供电与连接。" : odin],
    ["当前地图包", state.package || "未加载"], ["浏览器 WebSocket", socketState],
    ["ROS2 节点", state.rosKnown ? state.nodes.join("、") || "未发现必要节点" : "未知"],
    ["ROS2 话题", state.rosKnown ? state.topics.join("、") || "未发现必要话题" : "未知"],
    ["最近操作", jobLine],
  ]);
  buttons();
}

async function refreshMaps() {
  const request = ++mapRequest;
  get("maps-message").textContent = "正在读取地图目录…";
  try {
    const result = await api("/api/maps");
    if (request !== mapRequest) return;
    listing = result;
    if (!listing.some((map) => map.id === selected)) selected = null;
    get("maps-message").textContent = listing.length ? "" : "暂无可用 BIN。请先建图并保存。";
    const rows = listing.map((map) => {
      const row = document.createElement("tr");
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "map";
      input.checked = selected === map.id;
      input.addEventListener("change", () => { selected = map.id; buttons(); });
      label.append(input, map.name);
      row.insertCell().append(label);
      const modified = new Date(map.modified * 1000).toLocaleString("zh-CN");
      const created = map.created ? new Date(map.created * 1000).toLocaleString("zh-CN") : "未知（修改于 " + modified + "）";
      for (const value of [created, map.bin ? "有" : "缺失", map.pcd ? "已生成" : "未生成",
        map.package ? "已生成" : "未生成", map.current ? "当前地图" : "—"]) {
        row.insertCell().textContent = value;
      }
      return row;
    });
    get("map-rows").replaceChildren(...rows);
  } catch (error) {
    if (request === mapRequest) get("maps-message").textContent = "地图列表暂不可用。请刷新重试。";
  }
  buttons();
}

async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const wasOffline = !online;
    state = await api("/api/status");
    online = true;
    get("connection").textContent = "";
    const key = state.job ? [state.job.started, state.job.status].join(":") : "";
    if (page === "maps" && (wasOffline || key !== lastJob)) refreshMaps();
    lastJob = key;
  } catch (error) {
    online = false;
    get("connection").textContent = "暂时无法连接 AGX，正在重试。现场紧急情况请使用物理急停。";
  } finally {
    pollBusy = false;
    render();
  }
}

function navigate() {
  const requested = location.hash.slice(1);
  page = pages.has(requested) ? requested : "overview";
  render();
  if (page === "maps" && state && online) refreshMaps();
}

for (const button of actions) button.addEventListener("click", async () => {
  const action = button.dataset.action;
  if (!state || !online || pending || button.disabled) return;
  if (action === "mapping-start" && !confirm("现场已准备好，可以启动 Odin1 建图？")) return;
  pending = true;
  buttons();
  get("feedback").textContent = "";
  try {
    const body = mapActions.has(action) ? {mapId: selected} : {};
    const job = await api("/api/actions/" + action, {body, csrf: state.csrf});
    state.job = job;
    state.allowed = [];
    get("feedback").textContent = "请求已受理，请等待执行结果。";
    if (action === "load") location.hash = "navigation";
    if (action === "mapping-start") location.hash = "mapping";
  } catch (error) {
    get("feedback").textContent = error.status ? error.message : "请求结果未知，请查看当前任务。";
  } finally {
    pending = false;
    render();
    await poll();
  }
});

get("refresh-maps").addEventListener("click", refreshMaps);

for (const operation of ["prepare", "start", "stop"]) {
  get("motion-" + operation).addEventListener("click", async () => {
    if (!state || !online) return;
    let body = {};
    if (operation === "prepare") {
      if (!confirm("确认急停可用、范围清空、姿态稳定？准备运动会零速接管，可能改变机器狗姿态。")) return;
      body = {confirmed: true};
    }
    pending = true;
    render();
    try {
      const result = await api("/api/motion/" + operation, {body, csrf: state.csrf});
      get("motion-feedback").textContent = result.message;
    } catch (error) {
      get("motion-feedback").textContent = error.status ? error.message : "结果未知，请现场核对，必要时使用物理急停。";
    } finally {
      pending = false;
      await poll();
    }
  });
}

setInterval(async () => {
  if (!state?.motionGate?.owns || !["preparing", "prepared", "starting", "moving"].includes(state.motionGate.phase) ||
      page !== "navigation" || document.visibilityState !== "visible" || !online) return;
  try { await api("/api/motion/heartbeat", {body: {}, csrf: state.csrf}); } catch { /* watchdog locks on timeout */ }
}, 1000);
window.addEventListener("hashchange", navigate);
window.addEventListener("message", (event) => {
  const frame = get("planner");
  if (event.origin !== location.origin || event.source !== frame.contentWindow ||
      event.data?.source !== "odin3d-planner" || !frame.hasAttribute("src")) return;
  if (event.data.state === "connected") {
    socketState = "已连接";
    plannerState = "已连接，等待选择目标或接收已有路径";
  }
  if (event.data.state === "offline") {
    socketState = "未连接";
    plannerState = "连接已中断，请重新连接后核对路径";
  }
  if (["target", "planned", "error"].includes(event.data.state)) plannerState = event.data.detail;
  render();
});
navigate();
poll();
setInterval(poll, 1800);
