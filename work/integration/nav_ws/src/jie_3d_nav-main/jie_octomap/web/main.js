import * as THREE from "./vendor/three.module.js";
import { OrbitControls } from "./vendor/jsm/controls/OrbitControls.js";

const ROSLIB = window.ROSLIB;
if (!ROSLIB) {
  throw new Error("ROSLIB 加载失败，请检查 CDN 网络访问，或改为本地提供 roslib。");
}

const wsInput = document.getElementById("ws-url");
const advancedSettings = document.getElementById("advanced-settings");
const connectBtn = document.getElementById("connect-btn");
const reconnectBtn = document.getElementById("reconnect-btn");
const connStatus = document.getElementById("conn-status");
const relocalizationStatus = document.getElementById("relocalization-status");
const robotTfStatus = document.getElementById("robot-tf-status");
const selectionStatus = document.getElementById("selection-status");
const mapStatus = document.getElementById("map-status");
const canvas = document.getElementById("viewport");
const navigationConfirmModal = document.getElementById("navigation-confirm-modal");
const navigationConfirmMessage = document.getElementById("navigation-confirm-message");
const navigationConfirmStartBtn = document.getElementById("navigation-confirm-start");
const navigationConfirmCancelBtn = document.getElementById("navigation-confirm-cancel");
const setCurrentPoseBtn = document.getElementById("set-current-pose-btn");
const setNavigateBtn = document.getElementById("set-navigate-btn");
const setStartBtn = document.getElementById("set-start-btn");
const setGoalBtn = document.getElementById("set-goal-btn");
const stopNavigationBtn = document.getElementById("stop-navigation-btn");
const resetViewBtn = document.getElementById("reset-view-btn");
const joystickPad = document.getElementById("motion-joystick");
const joystickKnob = document.getElementById("motion-joystick-knob");
const manualRotationSlider = document.getElementById("manual-rotation-slider");
const manualVelocityDisplay = document.getElementById("manual-velocity-display");
const toggleOccupied = document.getElementById("toggle-occupied");
const toggleTraversable = document.getElementById("toggle-traversable");
const togglePreblocked = document.getElementById("toggle-preblocked");
const toggleRisk = document.getElementById("toggle-risk");
const toggleOdinCloud = document.getElementById("toggle-odin-cloud");
const odinCloudStatus = document.getElementById("odin-cloud-status");
const query = new URLSearchParams(window.location.search);
const cloudOnly = query.get("preview") === "odin";
const cloudIntervalMs = 250;
const cloudPointLimit = 60000;
const cloudByteLimit = 16 * 1024 * 1024;
toggleOdinCloud.checked = cloudOnly;
document.body.toggleAttribute("data-cloud-only", cloudOnly);

const consolePlanner = window.location.pathname.startsWith("/planner/");
function reportPlanner(state, detail = "") {
  if (consolePlanner) {
    window.parent.postMessage({ source: "odin3d-planner", state, detail }, window.location.origin);
  }
}
let consoleCanPlan = false;
if (consolePlanner) {
  window.addEventListener("message", (event) => {
    if (event.source !== window.parent || event.origin !== window.location.origin ||
        event.data?.source !== "odin3d-console") return;
    consoleCanPlan = event.data.canPlan === true;
    for (const button of [setNavigateBtn, setStartBtn, setGoalBtn]) button.disabled = !consoleCanPlan;
    if (!consoleCanPlan) setPlacementMode(null);
  });
  for (const button of [setNavigateBtn, setStartBtn, setGoalBtn]) button.disabled = true;
}
function defaultRosbridgeUrl() {
  if (consolePlanner) {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return protocol + "://" + window.location.host + "/planner/socket" + (cloudOnly ? "?preview=odin" : "");
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const port = query.get("ws_port") || "9090";
  return `${protocol}://${window.location.hostname || "localhost"}:${port}`;
}

wsInput.value = defaultRosbridgeUrl();
if (consolePlanner) {
  document.body.classList.add("console-planner");
}

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(canvas.clientWidth || window.innerWidth, canvas.clientHeight || window.innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x091116);
const cloudScene = new THREE.Scene();
cloudScene.background = scene.background;

const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 500);
camera.up.set(0, 0, 1);
camera.position.set(8, -10, 7);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.target.set(0, 0, 0.8);

function makeAxisArrow(direction, color, length, shaftRadius, headLength, headRadius) {
  const group = new THREE.Group();
  const shaftLength = Math.max(0.01, length - headLength);
  const shaftMaterial = new THREE.MeshStandardMaterial({
    color,
    roughness: 0.35,
    metalness: 0.08,
  });
  const headMaterial = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: 0.18,
    roughness: 0.25,
    metalness: 0.1,
  });

  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(shaftRadius, shaftRadius, shaftLength, 16),
    shaftMaterial,
  );
  shaft.position.y = shaftLength * 0.5;

  const head = new THREE.Mesh(
    new THREE.ConeGeometry(headRadius, headLength, 20),
    headMaterial,
  );
  head.position.y = shaftLength + headLength * 0.5;

  group.add(shaft);
  group.add(head);

  const up = new THREE.Vector3(0, 1, 0);
  group.quaternion.setFromUnitVectors(up, direction.clone().normalize());
  return group;
}

function makeAxes(length = 2.0) {
  const group = new THREE.Group();
  const shaftRadius = 0.035;
  const headLength = 0.22;
  const headRadius = 0.09;

  group.add(makeAxisArrow(new THREE.Vector3(1, 0, 0), 0xff5f5f, length, shaftRadius, headLength, headRadius));
  group.add(makeAxisArrow(new THREE.Vector3(0, 1, 0), 0x58ef74, length, shaftRadius, headLength, headRadius));
  group.add(makeAxisArrow(new THREE.Vector3(0, 0, 1), 0x53b7d8, length, shaftRadius, headLength, headRadius));
  return group;
}

function makeBox(sizeX, sizeY, sizeZ, color) {
  const geometry = new THREE.BoxGeometry(sizeX, sizeY, sizeZ);
  const material = new THREE.MeshStandardMaterial({
    color,
    roughness: 0.42,
    metalness: 0.05,
  });
  return new THREE.Mesh(geometry, material);
}

function buildSimpleDogModel() {
  const group = new THREE.Group();

  const body = makeBox(0.50, 0.22, 0.16, 0xf2f2f2);
  body.position.set(0, 0, 0.03);
  group.add(body);

  const head = makeBox(0.12, 0.12, 0.12, 0x151515);
  head.position.set(0.31, 0, 0.05);
  group.add(head);

  const hipColor = 0xeb3131;
  const legColor = 0xf2f2f2;
  // The URDF body origin is the center of the four hip axes.
  const hips = [
    [0.18, 0.13, 0],
    [0.18, -0.13, 0],
    [-0.18, 0.13, 0],
    [-0.18, -0.13, 0],
  ];

  hips.forEach(([x, y, z]) => {
    const hip = makeBox(0.06, 0.06, 0.06, hipColor);
    hip.position.set(x, y, z);
    group.add(hip);

    const upper = makeBox(0.04, 0.04, 0.22, legColor);
    upper.position.set(x, y, z - 0.14);
    group.add(upper);
  });

  group.visible = false;
  return group;
}

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const dirLight = new THREE.DirectionalLight(0xffffff, 1.1);
dirLight.position.set(10, -6, 14);
scene.add(dirLight);

const groundGrid = new THREE.GridHelper(30, 30, 0x34515c, 0x24353d);
groundGrid.rotation.x = Math.PI * 0.5;
scene.add(groundGrid);
scene.add(makeAxes(1.8));

let occupiedPointsObject = null;
let occupiedPickObject = null;
let preblockedPointsObject = null;
let traversablePointsObject = null;
let traversablePickObject = null;
let riskPointsObject = null;
let pathObject = null;
let trackingPointObject = null;
let startArrow = null;
let startCube = null;
let goalArrow = null;
let goalCube = null;
let robotObject = buildSimpleDogModel();
let voxelSize = 0.2;
let ros = null;
let startTopic = null;
let goalTopic = null;
let goalPoseTopic = null;
let initialPoseTopic = null;
let startNavigationTopic = null;
let stopNavigationTopic = null;
let dogBridgeStopTopic = null;
let cmdVelTopic = null;
let manualEnableTopic = null;
let manualReady = false;
let rotationPointerId = null;
const manualReadyBtn = document.getElementById("manual-ready-btn");
let tfTopic = null;
let tfStaticTopic = null;
let reconnectTimer = null;
let activePointerId = null;
let navigationDrag = null;
let placementMode = null;
let pendingNavigationGoal = null;
let pendingNavigationPath = null;
let navigationConfirmTimer = null;
let joystickActivePointerId = null;
let joystickLastPublishMs = 0;
let joystickRepeatTimer = null;
let joystickCurrentLinearX = 0;
let joystickCurrentLinearY = 0;
let joystickCurrentAngularZ = 0;
let mapHasBeenAutoFramed = false;
let latestOccupiedBounds = null;
let mapFrame = "map";
let odinCloudTopic = null;
let odinCloudObject = null;
let odinCloudFrame = "";
let odinCloudStamp = NaN;
let odinCloudBounds = null;
let odinCloudUpdatedAt = -Infinity;
let odinCloudCount = 0;
let odinCloudFrames = 0;
let odinCloudAligned = false;
let pendingOdinCloud = null;
let showingIndependentCloud = cloudOnly;
const cloudTf = new Map();
const tfState = new Map();
const odinTfProgress = new Map();
const joystickMaxLinearX = 0.10;
const joystickMaxLinearY = 0.10;
const joystickMaxAngularZ = 0.20;
const joystickDeadband = 0.12;
const joystickPublishIntervalMs = 80;


scene.add(robotObject);

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
function setConnectionStatus(text) {
  connStatus.textContent = text;
}

function setSelectionStatus(text) {
  selectionStatus.textContent = text;
}

function setRobotTfStatus(text) {
  if (robotTfStatus) {
    robotTfStatus.textContent = text;
  }
}

function setRelocalizationStatus(localized) {
  if (!relocalizationStatus) {
    return;
  }
  relocalizationStatus.textContent = localized ? "机器人定位成功" : "机器人尚未定位";
  relocalizationStatus.classList.toggle("ok", localized);
  relocalizationStatus.classList.toggle("fail", !localized);
}

function setMapStatus(text) {
  mapStatus.textContent = text;
}

function updateRendererSize() {
  const width = canvas.clientWidth || window.innerWidth;
  const height = canvas.clientHeight || Math.max(window.innerHeight - 260, 320);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function disposeObject(object) {
  if (!object) {
    return;
  }
  if (object.children && object.children.length > 0) {
    object.children.forEach((child) => disposeObject(child));
  }
  if (object.geometry) {
    object.geometry.dispose();
  }
  if (Array.isArray(object.material)) {
    object.material.forEach((material) => material.dispose());
  } else if (object.material) {
    object.material.dispose();
  }
}

function computePointBounds(points) {
  if (!points || points.length === 0) {
    return null;
  }
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (const point of points) {
    min.x = Math.min(min.x, point.x);
    min.y = Math.min(min.y, point.y);
    min.z = Math.min(min.z, point.z);
    max.x = Math.max(max.x, point.x);
    max.y = Math.max(max.y, point.y);
    max.z = Math.max(max.z, point.z);
  }
  if (!Number.isFinite(min.x) || !Number.isFinite(max.x)) {
    return null;
  }
  return { min, max };
}

function frameCameraToBounds(bounds) {
  if (!bounds) {
    return;
  }
  const center = bounds.min.clone().add(bounds.max).multiplyScalar(0.5);
  const size = bounds.max.clone().sub(bounds.min);
  const maxDim = Math.max(size.x, size.y, size.z, voxelSize * 8.0, 1.0);
  const fovRad = THREE.MathUtils.degToRad(camera.fov);
  const distance = Math.max(maxDim * 1.35, (maxDim * 0.5) / Math.tan(fovRad * 0.5));
  const direction = new THREE.Vector3(1.0, -1.25, 0.8).normalize();
  const position = center.clone().add(direction.multiplyScalar(distance));

  camera.position.copy(position);
  controls.target.copy(center);
  controls.minDistance = Math.max(voxelSize * 2.0, distance * 0.02);
  controls.maxDistance = Math.max(distance * 8.0, maxDim * 8.0);
  camera.near = Math.max(0.01, distance / 10000.0);
  camera.far = Math.max(1000.0, distance * 10.0, maxDim * 20.0);
  camera.updateProjectionMatrix();
  controls.update();
}

function resetMapView() {
  if (showingIndependentCloud) {
    frameCameraToBounds(odinCloudBounds);
    return;
  }
  if (!latestOccupiedBounds) {
    setMapStatus("尚未收到可用于重置视角的占据栅格。");
    return;
  }
  frameCameraToBounds(latestOccupiedBounds);
}

function makeVoxelGroup(marker, color, opacity = 1.0, visualScale = 1.0) {
  const sizeX = Math.max(0.02, Number(marker.scale.x || voxelSize)) * visualScale;
  const sizeY = Math.max(0.02, Number(marker.scale.y || voxelSize)) * visualScale;
  const sizeZ = Math.max(0.02, Number(marker.scale.z || voxelSize)) * visualScale;
  const geometry = new THREE.BoxGeometry(sizeX, sizeY, sizeZ);
  const fillMaterial = new THREE.MeshStandardMaterial({
    color,
    transparent: opacity < 0.999,
    opacity,
    roughness: 0.42,
    metalness: 0.04,
  });
  const edgeMaterial = new THREE.MeshBasicMaterial({
    color: 0x7a7a7a,
    wireframe: true,
    transparent: true,
    opacity: Math.min(1.0, opacity + 0.15),
  });
  const fillMesh = new THREE.InstancedMesh(geometry, fillMaterial, marker.points.length);
  const edgeMesh = new THREE.InstancedMesh(geometry, edgeMaterial, marker.points.length);
  fillMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  edgeMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);

  const matrix = new THREE.Matrix4();
  for (let i = 0; i < marker.points.length; i += 1) {
    const point = marker.points[i];
    matrix.makeTranslation(point.x, point.y, point.z);
    fillMesh.setMatrixAt(i, matrix);
    edgeMesh.setMatrixAt(i, matrix);
  }
  fillMesh.instanceMatrix.needsUpdate = true;
  edgeMesh.instanceMatrix.needsUpdate = true;

  const group = new THREE.Group();
  group.add(fillMesh);
  group.add(edgeMesh);
  return { group, pickMesh: fillMesh };
}

function setOccupiedPoints(marker) {
  if (occupiedPointsObject) {
    scene.remove(occupiedPointsObject);
    disposeObject(occupiedPointsObject);
  }
  occupiedPickObject = null;
  const frame = normalizeFrameId(marker.header?.frame_id) || "map";
  if (mapFrame !== frame) {
    odinCloudAligned = false;
    mapFrame = frame;
  }

  voxelSize = Math.max(0.02, Number(marker.scale.x || 0.2));

  let sumX = 0;
  let sumY = 0;
  let sumZ = 0;

  for (const point of marker.points) {
    sumX += point.x;
    sumY += point.y;
    sumZ += point.z;
  }

  const occupiedGroup = makeVoxelGroup(marker, 0xf6c85d, 1.0, 1.0);
  occupiedPointsObject = occupiedGroup.group;
  occupiedPickObject = occupiedGroup.pickMesh;
  scene.add(occupiedPointsObject);
  occupiedPointsObject.visible = toggleOccupied.checked;

  if (marker.points.length > 0) {
    const cx = sumX / marker.points.length;
    const cy = sumY / marker.points.length;
    const cz = sumZ / marker.points.length;
    latestOccupiedBounds = computePointBounds(marker.points);
    if (!showingIndependentCloud) {
      controls.target.set(cx, cy, cz);
      if (!mapHasBeenAutoFramed) {
        frameCameraToBounds(latestOccupiedBounds);
        mapHasBeenAutoFramed = true;
      }
    }
  } else {
    latestOccupiedBounds = null;
  }

  setMapStatus(`${marker.points.length} 个占据栅格，分辨率 ${voxelSize.toFixed(2)} 米`);
}

function setPreblockedPoints(marker) {
  if (preblockedPointsObject) {
    scene.remove(preblockedPointsObject);
    disposeObject(preblockedPointsObject);
    preblockedPointsObject = null;
  }

  if (!marker.points || marker.points.length === 0) {
    return;
  }

  preblockedPointsObject = makeVoxelGroup(marker, 0x4d83ff, 0.92, 1.0).group;
  scene.add(preblockedPointsObject);
  preblockedPointsObject.visible = togglePreblocked.checked;
}

function setTraversablePoints(marker) {
  if (traversablePointsObject) {
    scene.remove(traversablePointsObject);
    disposeObject(traversablePointsObject);
    traversablePointsObject = null;
  }
  traversablePickObject = null;

  if (!marker.points || marker.points.length === 0) {
    return;
  }

  const traversableGroup = makeVoxelGroup(marker, 0x58ef74, 0.22, 1.0);
  traversablePointsObject = traversableGroup.group;
  traversablePickObject = traversableGroup.pickMesh;
  scene.add(traversablePointsObject);
  traversablePointsObject.visible = toggleTraversable.checked;
}

function parsePointCloud2(msg, addPoint, maxPoints = Infinity, maxBytes = Infinity) {
  const { data, fields, width, height, point_step: pointStep, row_step: rowStep } = msg || {};
  if (!data || !Array.isArray(fields) ||
      ![width, height, pointStep, rowStep].every((n) => Number.isSafeInteger(n) && n > 0) ||
      rowStep < width * pointStep || rowStep * height > maxBytes) {
    return;
  }
  const size = rowStep * height;
  let byteArray;
  if (typeof data === "string" && data.length <= 4 * Math.ceil(size / 3)) {
    const decoded = atob(data);
    byteArray = new Uint8Array(decoded.length);
    for (let i = 0; i < decoded.length; i += 1) {
      byteArray[i] = decoded.charCodeAt(i);
    }
  } else if (data.length === size && (data instanceof Uint8Array || Array.isArray(data))) {
    byteArray = data instanceof Uint8Array ? data : new Uint8Array(data);
  }
  if (byteArray?.length !== size) {
    return;
  }
  const view = new DataView(byteArray.buffer, byteArray.byteOffset, byteArray.byteLength);
  const readers = ["x", "y", "z", "intensity"].map((name) => {
    const field = fields.find((item) => item.name === name);
    const bytes = { 7: 4, 8: 8 }[field?.datatype]; // PointField FLOAT32, FLOAT64
    if (!bytes || !Number.isInteger(field.offset) || field.offset < 0 || field.offset + bytes > pointStep) {
      return null;
    }
    return (base) => bytes === 4
      ? view.getFloat32(base + field.offset, !msg.is_bigendian)
      : view.getFloat64(base + field.offset, !msg.is_bigendian);
  });
  const [readX, readY, readZ, readIntensity] = readers;
  if (!readX || !readY || !readZ) {
    return;
  }
  const total = width * height;
  const samples = Math.min(total, maxPoints);
  for (let sample = 0; sample < samples; sample += 1) {
    const i = Math.floor(sample * total / samples);
    const base = Math.floor(i / width) * rowStep + (i % width) * pointStep;
    const x = readX(base);
    const y = readY(base);
    const z = readZ(base);
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
      addPoint(x, y, z, readIntensity?.(base) ?? 0);
    }
  }
}

function setRiskPoints(msg) {
  if (riskPointsObject) {
    scene.remove(riskPointsObject);
    disposeObject(riskPointsObject);
    riskPointsObject = null;
  }

  const points = [];
  parsePointCloud2(msg, (x, y, z, intensity) => points.push({ x, y, z, intensity }));
  if (points.length === 0) {
    return;
  }

  const scale = voxelSize;
  const group = new THREE.Group();
  points.forEach((point) => {
    const alpha = Math.min(0.95, Math.max(0.12, 0.12 + point.intensity * 0.83));
    const mesh = new THREE.Mesh(
      new THREE.BoxGeometry(scale, scale, scale),
      new THREE.MeshStandardMaterial({
        color: 0x2659ff,
        transparent: true,
        opacity: alpha,
        roughness: 0.38,
        metalness: 0.03,
      }),
    );
    mesh.position.set(point.x, point.y, point.z);
    group.add(mesh);
  });
  riskPointsObject = group;
  riskPointsObject.visible = toggleRisk.checked;
  scene.add(riskPointsObject);
}

function clearObject(object) {
  if (!object) {
    return;
  }
  scene.remove(object);
  if (object.geometry) {
    object.geometry.dispose();
  }
  if (object.material) {
    object.material.dispose();
  }
}

function makeCube(center, size, color) {
  const geometry = new THREE.BoxGeometry(size, size, size);
  const material = new THREE.MeshStandardMaterial({ color });
  const cube = new THREE.Mesh(geometry, material);
  cube.position.set(center.x, center.y, center.z);
  return cube;
}

function makeArrow(marker, color) {
  if (!marker.points || marker.points.length < 2) {
    return null;
  }
  const start = new THREE.Vector3(marker.points[0].x, marker.points[0].y, marker.points[0].z);
  const end = new THREE.Vector3(marker.points[1].x, marker.points[1].y, marker.points[1].z);
  const dir = end.clone().sub(start);
  const length = dir.length();
  if (length < 1e-6) {
    return null;
  }
  dir.normalize();
  return new THREE.ArrowHelper(dir, start, length, color, Math.max(voxelSize * 1.5, 0.25), Math.max(voxelSize, 0.18), Math.max(voxelSize * 0.65, 0.12));
}

function setSelectionMarkers(markerArray) {
  clearObject(startArrow);
  clearObject(startCube);
  clearObject(goalArrow);
  clearObject(goalCube);
  startArrow = null;
  startCube = null;
  goalArrow = null;
  goalCube = null;

  for (const marker of markerArray.markers) {
    if (marker.type === 0 && marker.id === 0) {
      startArrow = makeArrow(marker, 0x58ef74);
      if (startArrow) {
        scene.add(startArrow);
      }
    } else if (marker.type === 1 && marker.id === 2) {
      startCube = makeCube(marker.pose.position, marker.scale.x, 0x58ef74);
      scene.add(startCube);
    } else if (marker.type === 0 && marker.id === 1) {
      goalArrow = makeArrow(marker, 0xff6767);
      if (goalArrow) {
        scene.add(goalArrow);
      }
    } else if (marker.type === 1 && marker.id === 3) {
      goalCube = makeCube(marker.pose.position, marker.scale.x, 0xff6767);
      scene.add(goalCube);
    }
  }
}

function setPath(pathMsg) {
  if (pathObject) {
    scene.remove(pathObject);
    pathObject.geometry.dispose();
    if (pathObject.material) {
      pathObject.material.dispose();
    }
    pathObject = null;
  }

  if (!pathMsg.poses || pathMsg.poses.length < 2) {
    reportPlanner("error", "规划器未给出可显示的路径，请核对起终点和同层可通行区。");
    return;
  }

  const points = pathMsg.poses.map(
    (pose) =>
      new THREE.Vector3(
        pose.pose.position.x,
        pose.pose.position.y,
        pose.pose.position.z,
      ),
  );
  const curve = new THREE.CatmullRomCurve3(points);
  const geometry = new THREE.TubeGeometry(
    curve,
    Math.max(16, points.length * 3),
    Math.max(voxelSize * 0.22, 0.06),
    12,
    false,
  );
  const material = new THREE.MeshStandardMaterial({
    color: 0xb067ff,
    emissive: 0x4f2586,
    roughness: 0.28,
    metalness: 0.08,
  });
  pathObject = new THREE.Mesh(geometry, material);
  scene.add(pathObject);
  if (consolePlanner) {
    reportPlanner("planned", "路径已显示，共 " + points.length + " 个路径点。");
  } else {
    scheduleNavigationConfirmation(pathMsg);
  }
}

function setTrackingPoint(marker) {
  clearTrackingPointObjects();

  if (marker.action === 2) {
    return;
  }

  const scale = Math.max(
    0.08,
    Number(marker.scale?.x || marker.scale?.y || marker.scale?.z || voxelSize * 1.5),
  );
  const geometry = new THREE.SphereGeometry(scale * 0.5, 24, 16);
  const material = new THREE.MeshStandardMaterial({
    color: 0x24a6ff,
    emissive: 0x0b4e86,
    roughness: 0.22,
    metalness: 0.08,
  });
  trackingPointObject = new THREE.Mesh(geometry, material);
  trackingPointObject.name = "current-tracking-point";
  trackingPointObject.position.set(
    marker.pose.position.x,
    marker.pose.position.y,
    marker.pose.position.z,
  );
  scene.add(trackingPointObject);
}

function clearTrackingPointObjects() {
  const staleObjects = [];
  scene.traverse((object) => {
    if (object.name === "current-tracking-point") {
      staleObjects.push(object);
    }
  });

  for (const object of staleObjects) {
    if (object.parent) {
      object.parent.remove(object);
    } else {
      scene.remove(object);
    }
    disposeObject(object);
  }
  trackingPointObject = null;
}

function scheduleNavigationConfirmation(pathMsg) {
  if (!pendingNavigationGoal || !pathMsg.poses || pathMsg.poses.length < 2) {
    return;
  }
  if (navigationConfirmTimer) {
    window.clearTimeout(navigationConfirmTimer);
  }

  // goal_point and goal_pose are published back-to-back and can produce two plans.
  // Wait 2 seconds so the planned path is visible before asking for execution.
  navigationConfirmTimer = window.setTimeout(() => {
    navigationConfirmTimer = null;
    confirmNavigationExecution(pathMsg);
  }, 2000);
}

function publishStartNavigation(shouldStart) {
  if (consolePlanner) return;
  if (shouldStart && manualReady) {
    disarmWebManual();
  }
  if (!startNavigationTopic) {
    setSelectionStatus("ROSBridge 未连接，无法发送导航执行确认。");
    return;
  }
  startNavigationTopic.publish(new ROSLIB.Message({ data: shouldStart }));
}

function makeTwist(linearX = 0, linearY = 0, angularZ = 0) {
  return new ROSLIB.Message({
    linear: { x: linearX, y: linearY, z: 0 },
    angular: { x: 0, y: 0, z: angularZ },
  });
}

function updateManualVelocityDisplay() {
  if (!manualVelocityDisplay) {
    return;
  }
  manualVelocityDisplay.textContent =
    `x=${joystickCurrentLinearX.toFixed(3)} y=${joystickCurrentLinearY.toFixed(3)} wz=${joystickCurrentAngularZ.toFixed(3)}`;
}

function publishManualVelocity(force = false) {
  updateManualVelocityDisplay();
  if (!manualReady || !ros?.isConnected || !cmdVelTopic) {
    return;
  }
  const now = Date.now();
  if (!force && now - joystickLastPublishMs < joystickPublishIntervalMs) {
    return;
  }
  joystickLastPublishMs = now;
  const scale = Math.max(1, Math.hypot(joystickCurrentLinearX, joystickCurrentLinearY) / joystickMaxLinearX);
  cmdVelTopic.publish(makeTwist(
    joystickCurrentLinearX / scale, joystickCurrentLinearY / scale, joystickCurrentAngularZ,
  ));
}

function publishZeroVelocity() {
  joystickCurrentLinearX = 0;
  joystickCurrentLinearY = 0;
  joystickCurrentAngularZ = 0;
  if (manualRotationSlider) {
    manualRotationSlider.value = "0";
  }
  publishManualVelocity(true);
}

function applyJoystickSpeedCurve(normalizedValue, maxSpeed) {
  const sign = Math.sign(normalizedValue);
  const magnitude = Math.abs(normalizedValue);
  if (magnitude < joystickDeadband) {
    return 0;
  }

  const scaled = (magnitude - joystickDeadband) / (1 - joystickDeadband) * maxSpeed;
  return sign * scaled;
}

function disarmWebManual() {
  if (manualReady) {
    publishZeroVelocity();
    if (ros?.isConnected) {
      manualEnableTopic?.publish(new ROSLIB.Message({ data: false }));
    }
  }
  manualReady = false;
  joystickActivePointerId = null;
  rotationPointerId = null;
  resetJoystickKnob();
  manualRotationSlider.disabled = true;
  manualReadyBtn.textContent = "启用网页手动";
  window.clearInterval(joystickRepeatTimer);
  joystickRepeatTimer = null;
}

function toggleWebManual() {
  if (consolePlanner) return;
  if (manualReady) {
    disarmWebManual();
    setSelectionStatus("已退出网页手动。");
    return;
  }
  if (!ros?.isConnected || !cmdVelTopic) {
    setSelectionStatus("等待 ROSBridge 连接。");
    return;
  }
  stopPathTrackingForManualMotion();
  manualReady = true;
  manualRotationSlider.disabled = false;
  manualReadyBtn.textContent = "退出网页手动";
  publishZeroVelocity();
  joystickRepeatTimer = window.setInterval(() => publishManualVelocity(true), 100);
  manualEnableTopic.publish(new ROSLIB.Message({ data: true }));
  setSelectionStatus("已请求启用手动 Bridge。按住摇杆或旋转滑条，松开归零。");
}

function stopNavigation() {
  if (consolePlanner) return;
  disarmWebManual();
  if (!stopNavigationTopic) {
    setSelectionStatus("ROSBridge 未连接，无法发送停止导航命令。");
    return;
  }
  dogBridgeStopTopic?.publish(new ROSLIB.Message({ data: true }));
  stopPathTrackingForManualMotion();
  setSelectionStatus("已请求停止路径跟踪并退出 SDK Bridge。");
}

function stopPathTrackingForManualMotion() {
  if (!stopNavigationTopic) {
    return;
  }
  pendingNavigationGoal = null;
  pendingNavigationPath = null;
  hideNavigationConfirmModal();
  if (navigationConfirmTimer) {
    window.clearTimeout(navigationConfirmTimer);
    navigationConfirmTimer = null;
  }
  publishStartNavigation(false);
  stopNavigationTopic.publish(new ROSLIB.Message({ data: true }));
}

function resetJoystickKnob() {
  joystickKnob.style.transform = "translate(-50%, -50%)";
  joystickKnob.classList.remove("active");
}

function updateJoystickFromEvent(event, forcePublish = false) {
  const rect = joystickPad.getBoundingClientRect();
  const radius = rect.width * 0.5;
  const knobRadius = joystickKnob.getBoundingClientRect().width * 0.5;
  const maxOffset = Math.max(1, radius - knobRadius - 6);
  const centerX = rect.left + radius;
  const centerY = rect.top + radius;
  let dx = event.clientX - centerX;
  let dy = event.clientY - centerY;
  const distance = Math.hypot(dx, dy);
  if (distance > maxOffset) {
    dx = dx / distance * maxOffset;
    dy = dy / distance * maxOffset;
  }

  joystickKnob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;

  const normalizedX = dx / maxOffset;
  const normalizedY = dy / maxOffset;
  const linearX = applyJoystickSpeedCurve(-normalizedY, joystickMaxLinearX);
  const linearY = applyJoystickSpeedCurve(-normalizedX, joystickMaxLinearY);
  joystickCurrentLinearX = linearX;
  joystickCurrentLinearY = linearY;
  publishManualVelocity(forcePublish);
}

function startJoystickControl(event) {
  if (!manualReady || rotationPointerId !== null || joystickActivePointerId !== null) {
    return;
  }
  if (event.button !== undefined && event.button !== 0) {
    return;
  }
  event.preventDefault();
  joystickActivePointerId = event.pointerId;
  joystickKnob.classList.add("active");
  joystickKnob.setPointerCapture(event.pointerId);
  updateJoystickFromEvent(event, true);
  setSelectionStatus("手动运动控制中：松开小圆将回中并发送零速度。");
}

function moveJoystickControl(event) {
  if (joystickActivePointerId !== event.pointerId) {
    return;
  }
  event.preventDefault();
  updateJoystickFromEvent(event);
}

function endJoystickControl(event) {
  if (joystickActivePointerId !== event.pointerId) {
    return;
  }
  event.preventDefault();
  joystickActivePointerId = null;
  resetJoystickKnob();
  publishZeroVelocity();
  setSelectionStatus("摇杆已回中，已发送零速。");
}

function updateManualRotationFromSlider() {
  if (!manualReady || rotationPointerId === null) {
    return;
  }
  const normalized = Math.max(-1, Math.min(1, Number(manualRotationSlider.value) / 100));
  joystickCurrentAngularZ = applyJoystickSpeedCurve(-normalized, joystickMaxAngularZ);
  publishManualVelocity(true);
}

function endManualRotation(event) {
  if (rotationPointerId === event.pointerId) {
    rotationPointerId = null;
    publishZeroVelocity();
  }
}

function confirmNavigationExecution(pathMsg) {
  if (!pendingNavigationGoal) {
    return;
  }

  const goal = pendingNavigationGoal.goal;
  const yaw = pendingNavigationGoal.yaw;
  pendingNavigationPath = pathMsg;
  navigationConfirmMessage.textContent = [
    `路径点数：${pathMsg.poses.length}`,
    `目标位置：[${goal.x.toFixed(2)}, ${goal.y.toFixed(2)}, ${goal.z.toFixed(2)}]`,
    `目标朝向：${(yaw * 180 / Math.PI).toFixed(1)}°`,
    "",
    "点击“开始导航”后会请求路径跟踪。首次试走须在 AGX 端单独使能 SDK Bridge。",
    "点击“只显示路线”会显示三维路线，但不执行导航。",
  ].join("\n");
  navigationConfirmModal.hidden = false;
  setSelectionStatus("路径规划完成，请在弹窗中确认是否开始导航。");
}

function hideNavigationConfirmModal() {
  navigationConfirmModal.hidden = true;
}

function resolveNavigationConfirmation(shouldStart) {
  if (!pendingNavigationGoal && !pendingNavigationPath) {
    hideNavigationConfirmModal();
    return;
  }
  pendingNavigationGoal = null;
  pendingNavigationPath = null;
  hideNavigationConfirmModal();
  if (shouldStart && !resolveRobotPose()) {
    publishStartNavigation(false);
    setSelectionStatus("实测机体位姿不可用，恢复后请重新选导航目标。");
    return;
  }
  publishStartNavigation(shouldStart);
  setSelectionStatus(
    shouldStart
      ? "已请求开始路径跟踪。"
      : "已取消导航执行，仅显示规划路线，不进行路径跟踪。",
  );
}

function makePointStamped(x, y, z) {
  const now = Date.now();
  return new ROSLIB.Message({
    header: {
      frame_id: "map",
      stamp: {
        sec: Math.floor(now / 1000),
        nanosec: (now % 1000) * 1000000,
      },
    },
    point: { x, y, z },
  });
}

function makePoseStamped(x, y, z, yaw) {
  const now = Date.now();
  const halfYaw = yaw * 0.5;
  return new ROSLIB.Message({
    header: {
      frame_id: "map",
      stamp: {
        sec: Math.floor(now / 1000),
        nanosec: (now % 1000) * 1000000,
      },
    },
    pose: {
      position: { x, y, z },
      orientation: {
        x: 0,
        y: 0,
        z: Math.sin(halfYaw),
        w: Math.cos(halfYaw),
      },
    },
  });
}

function makePoseWithCovarianceStamped(x, y, z, yaw) {
  const now = Date.now();
  const halfYaw = yaw * 0.5;
  const covariance = new Array(36).fill(0);
  covariance[0] = 0.25;
  covariance[7] = 0.25;
  covariance[35] = 0.06853891909122467;
  return new ROSLIB.Message({
    header: {
      frame_id: "map",
      stamp: {
        sec: Math.floor(now / 1000),
        nanosec: (now % 1000) * 1000000,
      },
    },
    pose: {
      pose: {
        position: { x, y, z },
        orientation: {
          x: 0,
          y: 0,
          z: Math.sin(halfYaw),
          w: Math.cos(halfYaw),
        },
      },
      covariance,
    },
  });
}

function setPointVisual(kind, center, yaw) {
  const isStart = kind === "start";
  const color = isStart ? 0x58ef74 : 0xff6767;
  const arrowRef = isStart ? startArrow : goalArrow;
  const cubeRef = isStart ? startCube : goalCube;

  clearObject(arrowRef);
  clearObject(cubeRef);
  if (isStart) {
    startArrow = null;
    startCube = null;
  } else {
    goalArrow = null;
    goalCube = null;
  }

  const cube = makeCube(center, Math.max(voxelSize, 0.18), color);
  scene.add(cube);
  const direction = new THREE.Vector3(Math.cos(yaw), Math.sin(yaw), 0);
  const arrowLength = Math.max(voxelSize * 2.5, 0.45);
  const arrow = new THREE.ArrowHelper(
    direction.normalize(),
    new THREE.Vector3(center.x, center.y, center.z),
    arrowLength,
    color,
    Math.max(voxelSize * 1.5, 0.25),
    Math.max(voxelSize, 0.18),
  );
  scene.add(arrow);

  if (isStart) {
    startCube = cube;
    startArrow = arrow;
  } else {
    goalCube = cube;
    goalArrow = arrow;
  }
}

function yawFromQuaternion(qx, qy, qz, qw) {
  const sinyCosp = 2.0 * (qw * qz + qx * qy);
  const cosyCosp = 1.0 - 2.0 * (qy * qy + qz * qz);
  return Math.atan2(sinyCosp, cosyCosp);
}

function normalizeFrameId(frameId) {
  if (!frameId) {
    return "";
  }
  return String(frameId).replace(/^\/+/, "");
}

function tfKey(parent, child) {
  return `${normalizeFrameId(parent)}->${normalizeFrameId(child)}`;
}

function invertTransform(transform) {
  if (!transform) {
    return null;
  }
  const translation = transform.translation || { x: 0, y: 0, z: 0 };
  const rotation = transform.rotation || { x: 0, y: 0, z: 0, w: 1 };

  const q = new THREE.Quaternion(rotation.x, rotation.y, rotation.z, rotation.w).normalize();
  const qInv = q.clone().invert();
  const t = new THREE.Vector3(translation.x, translation.y, translation.z);
  const invT = t.clone().applyQuaternion(qInv).multiplyScalar(-1);

  return {
    translation: { x: invT.x, y: invT.y, z: invT.z },
    rotation: { x: qInv.x, y: qInv.y, z: qInv.z, w: qInv.w },
  };
}

function getTfTransform(parent, child) {
  const direct = tfState.get(tfKey(parent, child));
  if (direct) {
    return direct;
  }
  const reverse = tfState.get(tfKey(child, parent));
  if (reverse) {
    return invertTransform(reverse);
  }
  return null;
}

function composeTransforms(parentToMiddle, middleToChild) {
  const a = parentToMiddle.rotation;
  const b = middleToChild.rotation;
  const qa = new THREE.Quaternion(a.x, a.y, a.z, a.w).normalize();
  const qb = new THREE.Quaternion(b.x, b.y, b.z, b.w).normalize();
  const t = middleToChild.translation;
  const p = parentToMiddle.translation;
  const translation = new THREE.Vector3(t.x, t.y, t.z)
    .applyQuaternion(qa)
    .add(new THREE.Vector3(p.x, p.y, p.z));
  return { translation, rotation: qa.multiply(qb) };
}

function storeTransformMessage(msg) {
  if (!msg || !msg.transforms) {
    return;
  }
  for (const transformStamped of msg.transforms) {
    const key = tfKey(
      transformStamped.header.frame_id,
      transformStamped.child_frame_id,
    );
    tfState.set(key, transformStamped.transform);
    if (key === "odom->imu" || key === "odom->map" || key === "map->odom") {
      const stamp = transformStamped.header.stamp;
      const ns = stamp.sec * 1e9 + stamp.nanosec;
      const previous = odinTfProgress.get(key);
      if (previous?.ns !== ns) {
        const advancedAt = ns > 0 && ns > previous?.ns ? performance.now() : -Infinity;
        odinTfProgress.set(key, { ns, advancedAt });
      }
    }
  }
}

function stampSeconds(stamp) {
  return Number(stamp?.sec) + Number(stamp?.nanosec) / 1e9;
}

function storeCloudTransforms(msg, isStatic) {
  if (!isStatic && !toggleOdinCloud.checked) {
    return;
  }
  for (const { header, child_frame_id, transform } of msg.transforms || []) {
    const parent = normalizeFrameId(header.frame_id);
    const child = normalizeFrameId(child_frame_id);
    const t = transform.translation;
    const q = transform.rotation;
    const stamp = stampSeconds(header.stamp);
    if (!parent || !child || (!isStatic && !Number.isFinite(stamp)) ||
        ![t.x, t.y, t.z, q.x, q.y, q.z, q.w].every(Number.isFinite) ||
        Math.hypot(q.x, q.y, q.z, q.w) === 0) {
      continue;
    }
    const key = tfKey(parent, child);
    const edge = cloudTf.get(key) || { parent, child, samples: [] };
    const sample = {
      stamp,
      position: new THREE.Vector3(t.x, t.y, t.z),
      rotation: new THREE.Quaternion(q.x, q.y, q.z, q.w).normalize(),
    };
    if (isStatic) {
      edge.fixed = sample;
    } else {
      const last = edge.samples.at(-1);
      if (last && stamp < last.stamp - 2) {
        edge.samples.length = 0;
      } else if (last && stamp <= last.stamp) {
        continue;
      }
      edge.samples.push(sample);
      if (edge.samples.length > 120) {
        edge.samples.shift();
      }
    }
    cloudTf.set(key, edge);
  }
}

function transformAt(edge, stamp) {
  if (edge.fixed) {
    return edge.fixed;
  }
  const i = edge.samples.findIndex((sample) => sample.stamp >= stamp);
  const after = edge.samples[i];
  if (after?.stamp === stamp) {
    return after;
  }
  if (i < 1) {
    return null;
  }
  const before = edge.samples[i - 1];
  const span = after.stamp - before.stamp;
  if (span > 0.5) {
    return null;
  }
  const fraction = (stamp - before.stamp) / span;
  return {
    position: before.position.clone().lerp(after.position, fraction),
    rotation: before.rotation.clone().slerp(after.rotation, fraction),
  };
}

function findCloudTransform(target, source, stamp) {
  const queue = [{ frame: source, matrix: new THREE.Matrix4() }];
  const visited = new Set([source]);
  for (const { frame, matrix } of queue) {
    if (frame === target) {
      return matrix;
    }
    for (const edge of cloudTf.values()) {
      const forward = edge.child === frame;
      const next = forward ? edge.parent : edge.child;
      if ((!forward && edge.parent !== frame) || visited.has(next)) {
        continue;
      }
      const pose = transformAt(edge, stamp);
      if (!pose) {
        continue;
      }
      const step = new THREE.Matrix4().compose(pose.position, pose.rotation, new THREE.Vector3(1, 1, 1));
      if (!forward) {
        step.invert();
      }
      queue.push({ frame: next, matrix: step.multiply(matrix) });
      visited.add(next);
    }
  }
  return null;
}

function hasMapLayers() {
  return Boolean(latestOccupiedBounds || traversablePointsObject || preblockedPointsObject ||
    riskPointsObject || pathObject);
}

function refreshCloudView(now = performance.now()) {
  const previousIndependent = showingIndependentCloud;
  showingIndependentCloud = cloudOnly;
  if (odinCloudCount > 0) {
    const overlay = !cloudOnly && hasMapLayers();
    if (overlay && !odinCloudAligned) {
      const transform = findCloudTransform(mapFrame, odinCloudFrame, odinCloudStamp);
      if (transform) {
        odinCloudObject.matrix.copy(transform);
        odinCloudObject.matrixWorldNeedsUpdate = true;
        odinCloudAligned = true;
      }
    }
    showingIndependentCloud = !overlay || !odinCloudAligned;
    const targetScene = showingIndependentCloud ? cloudScene : scene;
    if (odinCloudObject.parent !== targetScene) {
      targetScene.add(odinCloudObject);
    }
    if (showingIndependentCloud && (odinCloudAligned || !previousIndependent)) {
      odinCloudObject.matrix.identity();
      odinCloudObject.matrixWorldNeedsUpdate = true;
      odinCloudAligned = false;
    }
    const mode = overlay && showingIndependentCloud
      ? `缺少点云时间戳对应的 TF（${odinCloudFrame} → ${mapFrame}），独立预览`
      : showingIndependentCloud ? "独立预览" : `已对齐至 ${mapFrame}`;
    const stale = now - odinCloudUpdatedAt > 3000 ? " · 已超过 3 秒未更新" : "";
    const status = `${mode} · ${odinCloudFrame} · ${odinCloudCount} 点 · 第 ${odinCloudFrames} 帧${stale}`;
    if (odinCloudStatus.textContent !== status) {
      odinCloudStatus.textContent = status;
    }
  }
  if (previousIndependent !== showingIndependentCloud) {
    frameCameraToBounds(showingIndependentCloud ? odinCloudBounds : latestOccupiedBounds);
  }
}

function receiveOdinCloud(msg) {
  const now = performance.now();
  if (!toggleOdinCloud.checked || now - odinCloudUpdatedAt < cloudIntervalMs - 10) {
    return;
  }
  if (msg.row_step * msg.height > cloudByteLimit ||
      msg.data?.length > 4 * Math.ceil(cloudByteLimit / 3)) {
    odinCloudStatus.textContent = "Odin1 点云超过 16 MiB。";
    return;
  }
  const frame = normalizeFrameId(msg.header?.frame_id);
  if (!frame) {
    odinCloudStatus.textContent = "Odin1 点云消息缺少 frame_id。";
    return;
  }
  const stamp = stampSeconds(msg.header.stamp);
  // Dynamic TF for the same sensor timestamp can arrive after the cloud.
  pendingOdinCloud = { msg, frame, stamp, deadline: pendingOdinCloud?.deadline ?? now + 300 };
  processPendingOdinCloud(now);
}

function processPendingOdinCloud(now) {
  if (!pendingOdinCloud) {
    return;
  }
  const { msg, frame, stamp, deadline } = pendingOdinCloud;
  const overlay = !cloudOnly && hasMapLayers();
  const transform = overlay ? findCloudTransform(mapFrame, frame, stamp) : null;
  if (overlay && !transform && now < deadline) {
    return;
  }
  pendingOdinCloud = null;
  if (!odinCloudObject) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position",
      new THREE.BufferAttribute(new Float32Array(cloudPointLimit * 3), 3).setUsage(THREE.DynamicDrawUsage));
    odinCloudObject = new THREE.Points(geometry,
      new THREE.PointsMaterial({ color: 0x39d9f7, size: 2, sizeAttenuation: false }));
    odinCloudObject.matrixAutoUpdate = false;
    odinCloudObject.frustumCulled = false;
  }
  const positions = odinCloudObject.geometry.getAttribute("position");
  const min = new THREE.Vector3(Infinity, Infinity, Infinity);
  const max = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  let count = 0;
  try {
    parsePointCloud2(msg, (x, y, z) => {
      positions.setXYZ(count++, x, y, z);
      min.set(Math.min(min.x, x), Math.min(min.y, y), Math.min(min.z, z));
      max.set(Math.max(max.x, x), Math.max(max.y, y), Math.max(max.z, z));
    }, cloudPointLimit, cloudByteLimit);
  } catch (error) {
    console.warn("Odin1 PointCloud2 decode failed", error);
  }
  if (!count) {
    odinCloudStatus.textContent = "Odin1 点云为空、格式不支持或超过 16 MiB。";
    return;
  }
  const firstInFrame = odinCloudFrame !== frame;
  odinCloudObject.geometry.setDrawRange(0, count);
  positions.needsUpdate = true;
  odinCloudCount = count;
  odinCloudFrame = frame;
  odinCloudStamp = stamp;
  odinCloudBounds = { min, max };
  odinCloudUpdatedAt = now;
  odinCloudFrames += 1;
  odinCloudAligned = Boolean(transform);
  odinCloudObject.matrix.copy(transform || new THREE.Matrix4());
  odinCloudObject.matrixWorldNeedsUpdate = true;
  refreshCloudView(now);
  if (showingIndependentCloud && firstInFrame) {
    frameCameraToBounds(odinCloudBounds);
  }
}

function clearOdinCloud() {
  pendingOdinCloud = null;
  if (odinCloudObject) {
    odinCloudObject.removeFromParent();
    disposeObject(odinCloudObject);
    odinCloudObject = null;
  }
  odinCloudFrame = "";
  odinCloudBounds = null;
  odinCloudCount = 0;
  odinCloudFrames = 0;
  odinCloudUpdatedAt = -Infinity;
  refreshCloudView();
}

function syncOdinCloudSubscription() {
  if (odinCloudTopic) {
    odinCloudTopic.unsubscribe();
    odinCloudTopic = null;
  }
  clearOdinCloud();
  odinCloudStatus.textContent = toggleOdinCloud.checked
    ? "等待 /odin1/cloud_slam 点云…"
    : "Odin1 点云已关闭。";
  if (toggleOdinCloud.checked && ros?.isConnected) {
    odinCloudTopic = new ROSLIB.Topic({
      ros,
      name: "/odin1/cloud_slam",
      messageType: "sensor_msgs/PointCloud2",
      throttle_rate: cloudIntervalMs,
      queue_length: 1,
    });
    odinCloudTopic.subscribe(receiveOdinCloud);
  }
}

function updateRelocalizationStatus() {
  setRelocalizationStatus(Boolean(getTfTransform("map", "odom")));
}

function resolveRobotPose() {
  const mapToOdom = getTfTransform("map", "odom");
  const odomToImu = getTfTransform("odom", "imu");
  const imuToBody = getTfTransform("imu", "robot_base_link");
  const mapEdge = tfState.has("map->odom") ? "map->odom" : "odom->map";
  const now = performance.now();
  const fresh = (key) => now - (odinTfProgress.get(key)?.advancedAt ?? -Infinity) < 3000;

  if (!imuToBody) {
    setRobotTfStatus("等待实测外参 imu → robot_base_link");
    return null;
  }
  if (!mapToOdom || !odomToImu || !fresh(mapEdge) || !fresh("odom->imu")) {
    setRobotTfStatus("等待实时 map → odom → imu");
    return null;
  }

  const { translation, rotation } = composeTransforms(
    composeTransforms(mapToOdom, odomToImu),
    imuToBody,
  );
  setRobotTfStatus("使用 map → odom → imu → robot_base_link");
  return {
    x: translation.x, y: translation.y, z: translation.z,
    yaw: yawFromQuaternion(rotation.x, rotation.y, rotation.z, rotation.w),
  };
}

function updateRobotVisual() {
  if (!robotObject) {
    return;
  }
  const robotPose = resolveRobotPose();
  if (!robotPose) {
    robotObject.visible = false;
    return;
  }

  robotObject.visible = true;
  robotObject.position.set(robotPose.x, robotPose.y, robotPose.z);
  robotObject.rotation.set(0, 0, robotPose.yaw);
}

function refreshLayerVisibility() {
  if (occupiedPointsObject) {
    occupiedPointsObject.visible = toggleOccupied.checked;
  }
  if (preblockedPointsObject) {
    preblockedPointsObject.visible = togglePreblocked.checked;
  }
  if (traversablePointsObject) {
    traversablePointsObject.visible = toggleTraversable.checked;
  }
  if (riskPointsObject) {
    riskPointsObject.visible = toggleRisk.checked;
  }
}

function publishStartPose(intersectionPoint, yaw) {
  if (consolePlanner && !consoleCanPlan) return;
  if (!startTopic) {
    setSelectionStatus("ROSBridge 未连接。");
    return;
  }
  startTopic.publish(makePointStamped(intersectionPoint.x, intersectionPoint.y, intersectionPoint.z));
  setPointVisual("start", intersectionPoint, yaw);
  setSelectionStatus(
    `起始点已设置：[${intersectionPoint.x.toFixed(2)}, ${intersectionPoint.y.toFixed(2)}, ${intersectionPoint.z.toFixed(2)}]，朝向 ${(yaw * 180 / Math.PI).toFixed(1)}°。`,
  );
}

function publishCurrentPose(intersectionPoint, yaw) {
  if (!initialPoseTopic) {
    setSelectionStatus("ROSBridge 未连接。");
    return;
  }
  initialPoseTopic.publish(
    makePoseWithCovarianceStamped(
      intersectionPoint.x,
      intersectionPoint.y,
      intersectionPoint.z,
      yaw,
    ),
  );
  clearObject(startArrow);
  clearObject(startCube);
  startArrow = null;
  startCube = null;
  setSelectionStatus(
    `当前姿态已设置：[${intersectionPoint.x.toFixed(2)}, ${intersectionPoint.y.toFixed(2)}, ${intersectionPoint.z.toFixed(2)}]，朝向 ${(yaw * 180 / Math.PI).toFixed(1)}°。`,
  );
}

function publishGoalPose(intersectionPoint, yaw) {
  if (consolePlanner && !consoleCanPlan) return;
  if (!goalTopic || !goalPoseTopic) {
    setSelectionStatus("ROSBridge 未连接。");
    return;
  }
  reportPlanner("target", "已选择目标，正在等待规划结果。");
  goalTopic.publish(makePointStamped(intersectionPoint.x, intersectionPoint.y, intersectionPoint.z));
  goalPoseTopic.publish(makePoseStamped(intersectionPoint.x, intersectionPoint.y, intersectionPoint.z, yaw));
  setPointVisual("goal", intersectionPoint, yaw);
  setSelectionStatus(
    `目标点已设置：[${intersectionPoint.x.toFixed(2)}, ${intersectionPoint.y.toFixed(2)}, ${intersectionPoint.z.toFixed(2)}]，朝向 ${(yaw * 180 / Math.PI).toFixed(1)}°，正在规划。`,
  );
}

function publishNavigationGoal(intersectionPoint, yaw) {
  if (consolePlanner && !consoleCanPlan) return;
  if (!startTopic || !goalTopic || !goalPoseTopic || !startNavigationTopic) {
    setSelectionStatus("ROSBridge 未连接。");
    return;
  }
  const robotPose = resolveRobotPose();
  if (!robotPose) {
    setSelectionStatus("实测机体位姿不可用，无法从实时位置规划。");
    return;
  }
  publishStartNavigation(false);
  reportPlanner("target", "已选择目标，正在等待规划结果。");
  pendingNavigationGoal = {
    goal: { x: intersectionPoint.x, y: intersectionPoint.y, z: intersectionPoint.z },
    yaw,
  };
  startTopic.publish(makePointStamped(robotPose.x, robotPose.y, robotPose.z));
  goalTopic.publish(makePointStamped(intersectionPoint.x, intersectionPoint.y, intersectionPoint.z));
  goalPoseTopic.publish(
    makePoseStamped(intersectionPoint.x, intersectionPoint.y, intersectionPoint.z, yaw),
  );
  setPointVisual("start", { x: robotPose.x, y: robotPose.y, z: robotPose.z }, robotPose.yaw);
  setPointVisual("goal", intersectionPoint, yaw);
  setSelectionStatus(
    `导航目标已设置：[${intersectionPoint.x.toFixed(2)}, ${intersectionPoint.y.toFixed(2)}, ${intersectionPoint.z.toFixed(2)}]，朝向 ${(yaw * 180 / Math.PI).toFixed(1)}°。${consolePlanner ? "正在规划路径。" : "正在规划路径，路径显示后请确认是否开始导航。"}`,
  );
}

function updatePointerFromEvent(event) {
  const rect = canvas.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
}

function pickTraversablePoint(event) {
  if (!traversablePickObject) {
    return null;
  }
  updatePointerFromEvent(event);
  const hits = raycaster.intersectObject(traversablePickObject);
  if (hits.length === 0) {
    return null;
  }
  return hits[0].point.clone();
}

function pickPointOnHeightPlane(event, planeZ) {
  updatePointerFromEvent(event);
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -planeZ);
  const hit = new THREE.Vector3();
  const ok = raycaster.ray.intersectPlane(plane, hit);
  return ok ? hit : null;
}

function computeYawFromPoints(start, end, fallbackYaw = 0) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) {
    return fallbackYaw;
  }
  return Math.atan2(dy, dx);
}

function scheduleReconnect() {
  if (reconnectTimer) {
    return;
  }
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    connectRosbridge(wsInput.value.trim(), false);
  }, 1500);
}

function createRosbridgeDecoder() {
  const pending = new Map();
  return (data, receive) => {
    const packet = JSON.parse(data);
    if (packet.op !== "fragment") {
      receive(packet);
      return;
    }

    const { id, num, total } = packet;
    if (!Number.isInteger(num) || !Number.isInteger(total) ||
        num < 0 || num >= total || typeof packet.data !== "string") {
      return;
    }
    if (!pending.has(id)) {
      pending.set(id, { parts: new Map(), expiry: setTimeout(() => pending.delete(id), 15000) });
    }
    const { parts, expiry } = pending.get(id);
    parts.set(num, packet.data);
    if (parts.size === total) {
      clearTimeout(expiry);
      pending.delete(id);
      const serialized = Array.from({ length: total }, (_, index) => parts.get(index)).join("");
      receive(JSON.parse(serialized));
    }
  };
}

function connectRosbridge(url = wsInput.value.trim(), manual = false) {
  wsInput.value = url;
  window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (ros) {
    disarmWebManual();
    const previous = ros;
    ros = null;
    syncOdinCloudSubscription();
    previous.close();
  }
  tfState.clear();
  odinTfProgress.clear();
  cloudTf.clear();

  setConnectionStatus("Connecting");
  setSelectionStatus(`正在连接 ${url} ...`);

  const connection = new ROSLIB.Ros({ url, transportOptions: { decoder: createRosbridgeDecoder() } });
  ros = connection;

  ros.on("connection", () => {
    if (ros !== connection) return;
    setConnectionStatus("已连接");
    reportPlanner("connected");
    advancedSettings.open = false;
    syncOdinCloudSubscription();
    if (cloudOnly) return;
    setSelectionStatus("已连接。点击“导航目标”“起始点”或“目标点”，再在可通行栅格上按下、拖动、松开设置姿态。");

    startTopic = new ROSLIB.Topic({
      ros,
      name: "/start_point",
      messageType: "geometry_msgs/PointStamped",
    });
    goalTopic = new ROSLIB.Topic({
      ros,
      name: "/goal_point",
      messageType: "geometry_msgs/PointStamped",
    });
    goalPoseTopic = new ROSLIB.Topic({
      ros,
      name: "/goal_pose",
      messageType: "geometry_msgs/PoseStamped",
    });
    startNavigationTopic = new ROSLIB.Topic({
      ros,
      name: "/start_navigation",
      messageType: "std_msgs/Bool",
    });
    stopNavigationTopic = new ROSLIB.Topic({
      ros,
      name: "/stop_navigation",
      messageType: "std_msgs/Bool",
    });
    dogBridgeStopTopic = new ROSLIB.Topic({
      ros,
      name: "/dog_bridge/stop",
      messageType: "std_msgs/Bool",
    });
    cmdVelTopic = new ROSLIB.Topic({
      ros, name: "/dog_bridge/manual_cmd_vel", messageType: "geometry_msgs/Twist",
    });
    manualEnableTopic = new ROSLIB.Topic({
      ros, name: "/dog_bridge/manual_enable", messageType: "std_msgs/Bool",
    });
    const writableTopics = consolePlanner
      ? [startTopic, goalTopic, goalPoseTopic]
      : [startTopic, goalTopic, goalPoseTopic, startNavigationTopic, stopNavigationTopic,
         dogBridgeStopTopic, cmdVelTopic, manualEnableTopic];
    for (const topic of writableTopics) {
      topic.advertise();
    }
    initialPoseTopic = new ROSLIB.Topic({
      ros,
      name: "/initialpose",
      messageType: "geometry_msgs/PoseWithCovarianceStamped",
    });

    const occupiedTopic = new ROSLIB.Topic({
      ros,
      name: "/octomap_occupied_markers",
      messageType: "visualization_msgs/Marker",
    });
    occupiedTopic.subscribe(setOccupiedPoints);

    const preblockedTopic = new ROSLIB.Topic({
      ros,
      name: "/preblocked_cells_markers",
      messageType: "visualization_msgs/Marker",
    });
    preblockedTopic.subscribe(setPreblockedPoints);

    const traversableTopic = new ROSLIB.Topic({
      ros,
      name: "/traversable_cells_markers",
      messageType: "visualization_msgs/Marker",
    });
    traversableTopic.subscribe(setTraversablePoints);

    const riskTopic = new ROSLIB.Topic({
      ros,
      name: "/risk_cost_cells",
      messageType: "sensor_msgs/PointCloud2",
    });
    riskTopic.subscribe(setRiskPoints);

    const selectionTopic = new ROSLIB.Topic({
      ros,
      name: "/selection_markers",
      messageType: "visualization_msgs/MarkerArray",
    });
    selectionTopic.subscribe(setSelectionMarkers);

    const pathTopic = new ROSLIB.Topic({
      ros,
      name: "/planned_path",
      messageType: "nav_msgs/Path",
    });
    pathTopic.subscribe(setPath);

    const trackingPointTopic = new ROSLIB.Topic({
      ros,
      name: "/tracking_point_marker",
      messageType: "visualization_msgs/Marker",
    });
    trackingPointTopic.subscribe(setTrackingPoint);

    const statusTopic = new ROSLIB.Topic({
      ros,
      name: "/web_selection_status",
      messageType: "std_msgs/String",
    });
    statusTopic.subscribe((msg) => setSelectionStatus(msg.data));

    tfTopic = new ROSLIB.Topic({
      ros,
      name: "/tf",
      messageType: "tf2_msgs/TFMessage",
    });
    tfTopic.subscribe((msg) => {
      storeTransformMessage(msg);
      storeCloudTransforms(msg, false);
    });

    tfStaticTopic = new ROSLIB.Topic({
      ros,
      name: "/odin_cyvet/body_tf_static",
      messageType: "tf2_msgs/TFMessage",
    });
    tfStaticTopic.subscribe((msg) => {
      storeTransformMessage(msg);
      storeCloudTransforms(msg, true);
    });
    updateRelocalizationStatus();
  });

  ros.on("error", (error) => {
    if (ros !== connection) return;
    setConnectionStatus("错误");
    reportPlanner("offline");
    setSelectionStatus(`ROSBridge 错误：${error}`);
    advancedSettings.open = true;
  });

  ros.on("close", () => {
    if (ros !== connection) return;
    disarmWebManual();
    manualEnableTopic = null;
    setConnectionStatus("未连接");
    reportPlanner("offline");
    startTopic = null;
    goalTopic = null;
    goalPoseTopic = null;
    initialPoseTopic = null;
    startNavigationTopic = null;
    stopNavigationTopic = null;
    dogBridgeStopTopic = null;
    cmdVelTopic = null;
    tfTopic = null;
    tfStaticTopic = null;
    tfState.clear();
    odinTfProgress.clear();
    cloudTf.clear();
    odinCloudTopic = null;
    syncOdinCloudSubscription();
    if (toggleOdinCloud.checked) {
      odinCloudStatus.textContent = "ROSBridge 已断开。";
    }
    updateRelocalizationStatus();
    pendingNavigationGoal = null;
    pendingNavigationPath = null;
    hideNavigationConfirmModal();
    if (navigationConfirmTimer) {
      window.clearTimeout(navigationConfirmTimer);
      navigationConfirmTimer = null;
    }
    advancedSettings.open = true;
    if (!manual) {
      scheduleReconnect();
    }
  });
}

function setPlacementMode(mode) {
  placementMode = mode;
  if (setCurrentPoseBtn) {
    setCurrentPoseBtn.classList.toggle("active", mode === "current_pose");
  }
  setNavigateBtn.classList.toggle("active", mode === "navigate");
  setStartBtn.classList.toggle("active", mode === "start");
  setGoalBtn.classList.toggle("active", mode === "goal");
  if (mode === "current_pose") {
    setSelectionStatus("当前姿态模式：在绿色可通行栅格上按下，拖动调整朝向，松开确认。");
  } else if (mode === "navigate") {
    setSelectionStatus("导航目标模式：松开后先规划并显示路线，再确认是否开始导航。");
  } else if (mode === "start") {
    setSelectionStatus("起始点模式：在绿色可通行栅格上按下，拖动调整朝向，松开确认。");
  } else if (mode === "goal") {
    setSelectionStatus("目标点模式：在绿色可通行栅格上按下，拖动调整朝向，松开确认并开始规划。");
  } else {
    setSelectionStatus("已连接。点击“导航目标”“起始点”或“目标点”，再在可通行栅格上按下、拖动、松开设置姿态。");
  }
}

if (setCurrentPoseBtn) {
  setCurrentPoseBtn.addEventListener("click", () => setPlacementMode("current_pose"));
}
setNavigateBtn.addEventListener("click", () => setPlacementMode("navigate"));
setStartBtn.addEventListener("click", () => setPlacementMode("start"));
setGoalBtn.addEventListener("click", () => setPlacementMode("goal"));
stopNavigationBtn.addEventListener("click", stopNavigation);
joystickKnob.addEventListener("pointerdown", startJoystickControl);
joystickKnob.addEventListener("pointermove", moveJoystickControl);
joystickKnob.addEventListener("pointerup", endJoystickControl);
joystickKnob.addEventListener("pointercancel", endJoystickControl);
joystickKnob.addEventListener("lostpointercapture", endJoystickControl);
manualReadyBtn.addEventListener("click", toggleWebManual);
manualRotationSlider.addEventListener("pointerdown", (event) => {
  if (!manualReady || event.button !== 0 || joystickActivePointerId !== null ||
      rotationPointerId !== null) {
    event.preventDefault();
    return;
  }
  rotationPointerId = event.pointerId;
  manualRotationSlider.setPointerCapture(event.pointerId);
});
manualRotationSlider.addEventListener("input", updateManualRotationFromSlider);
manualRotationSlider.addEventListener("keydown", (event) => event.preventDefault());
for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) {
  manualRotationSlider.addEventListener(event, endManualRotation);
}
window.addEventListener("blur", disarmWebManual);
window.addEventListener("pagehide", disarmWebManual);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    disarmWebManual();
  }
});
navigationConfirmStartBtn.addEventListener("click", () => resolveNavigationConfirmation(true));
navigationConfirmCancelBtn.addEventListener("click", () => resolveNavigationConfirmation(false));
toggleOccupied.addEventListener("change", refreshLayerVisibility);
toggleTraversable.addEventListener("change", refreshLayerVisibility);
togglePreblocked.addEventListener("change", refreshLayerVisibility);
toggleRisk.addEventListener("change", refreshLayerVisibility);
toggleOdinCloud.addEventListener("change", syncOdinCloudSubscription);
resetViewBtn.addEventListener("click", resetMapView);

canvas.addEventListener("pointerdown", (event) => {
  if (showingIndependentCloud) {
    return;
  }
  if (!placementMode) {
    setSelectionStatus("请先点击“导航目标”“起始点”或“目标点”按钮。");
    return;
  }
  if (event.button !== 0) {
    return;
  }
  const point = pickTraversablePoint(event);
  if (!point) {
    setSelectionStatus("没有点中可通行栅格，请按下绿色可通行栅格设置导航目标。");
    return;
  }
  activePointerId = event.pointerId;
  navigationDrag = {
    start: point,
    yaw: 0,
    mode: placementMode,
  };
  controls.enabled = false;
  canvas.setPointerCapture(event.pointerId);
  setPointVisual(
    placementMode === "current_pose" || placementMode === "start" ? "start" : "goal",
    point,
    0,
  );
  setSelectionStatus(
    placementMode === "current_pose"
      ? "当前姿态位置已设置。保持按下并拖动以调整绿色箭头朝向。"
      : placementMode === "navigate"
        ? "导航目标位置已设置。保持按下并拖动以调整红色箭头朝向。"
        : placementMode === "start"
          ? "起始点位置已设置。保持按下并拖动以调整绿色箭头朝向。"
          : "目标点位置已设置。保持按下并拖动以调整红色箭头朝向。"
  );
});

canvas.addEventListener("pointermove", (event) => {
  if (activePointerId !== event.pointerId || !navigationDrag) {
    return;
  }
  const point = pickPointOnHeightPlane(event, navigationDrag.start.z);
  if (!point) {
    return;
  }
  navigationDrag.yaw = computeYawFromPoints(navigationDrag.start, point, navigationDrag.yaw);
  setPointVisual(
    navigationDrag.mode === "current_pose" || navigationDrag.mode === "start" ? "start" : "goal",
    navigationDrag.start,
    navigationDrag.yaw,
  );
});

canvas.addEventListener("pointerup", (event) => {
  if (activePointerId !== event.pointerId || !navigationDrag) {
    return;
  }
  event.preventDefault();
  const point = pickPointOnHeightPlane(event, navigationDrag.start.z);
  if (point) {
    navigationDrag.yaw = computeYawFromPoints(navigationDrag.start, point, navigationDrag.yaw);
  }
  if (navigationDrag.mode === "current_pose") {
    publishCurrentPose(navigationDrag.start, navigationDrag.yaw);
  } else if (navigationDrag.mode === "start") {
    publishStartPose(navigationDrag.start, navigationDrag.yaw);
  } else if (navigationDrag.mode === "goal") {
    publishGoalPose(navigationDrag.start, navigationDrag.yaw);
  } else {
    publishNavigationGoal(navigationDrag.start, navigationDrag.yaw);
  }
  navigationDrag = null;
  activePointerId = null;
  controls.enabled = true;
  setPlacementMode(null);
  if (canvas.hasPointerCapture(event.pointerId)) {
    canvas.releasePointerCapture(event.pointerId);
  }
});

canvas.addEventListener("pointercancel", (event) => {
  if (activePointerId !== event.pointerId) {
    return;
  }
  navigationDrag = null;
  activePointerId = null;
  controls.enabled = true;
  setPlacementMode(null);
  if (canvas.hasPointerCapture(event.pointerId)) {
    canvas.releasePointerCapture(event.pointerId);
  }
});

connectBtn.addEventListener("click", () => connectRosbridge(wsInput.value.trim(), true));
reconnectBtn.addEventListener("click", () => connectRosbridge(defaultRosbridgeUrl(), true));
window.addEventListener("resize", updateRendererSize);
updateRendererSize();
connectRosbridge(defaultRosbridgeUrl(), false);

function animate() {
  controls.update();
  updateRobotVisual();
  processPendingOdinCloud(performance.now());
  refreshCloudView();
  renderer.render(showingIndependentCloud ? cloudScene : scene, camera);
  requestAnimationFrame(animate);
}

window.setInterval(updateRelocalizationStatus, 1000);

animate();
