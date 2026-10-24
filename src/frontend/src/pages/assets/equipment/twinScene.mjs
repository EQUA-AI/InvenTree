/**
 * AIMMS reference equipment viewer.
 *
 * Authored conceptual geometry, not OEM CAD, a dimensional drawing, or an
 * assessment of the equipment. No remote assets or physical animation are
 * used. The native mimic can supply a motor alarm; only an unacknowledged
 * alarm adds a timed redraw, with a static alternative for reduced motion.
 *
 * create(host, { selected, mode, labels, cameraState, onSelect, onReady,
 *                onError, onProject, hotspots = true })
 *
 * The returned API supports destroy, select, setMode, setLabels, setView,
 * zoom (distance multiplier), focus, reset, getState and getProjectedParts.
 * Selection preserves the camera. Mode changes fit the assembly while
 * preserving its viewing direction. reset() resets the view in the current
 * mode. cameraState accepts { position: [x,y,z], target: [x,y,z] }.
 * onProject receives (projectedParts, { width, height, mode, selected }).
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const VERSION = '1.0.0';
const TAU = Math.PI * 2;
const MODES = new Set(['assembled', 'cutaway', 'exploded']);
const PARTS = Object.freeze([
  { id: 'machine', number: 0, label: 'Complete pump train', parent: null, description: 'Conceptual vertical dry-installed centrifugal pump assembly.' },
  { id: 'motor', number: 1, label: 'Electric motor', parent: 'machine', description: 'Finned enclosure, terminal box, stator, rotor and motor shaft.' },
  { id: 'bearing', number: 2, label: 'Motor drive-end bearing', parent: 'motor', description: 'Open annular races, rolling elements and cage at the motor drive end.' },
  { id: 'coupling', number: 3, label: 'Flexible coupling', parent: 'machine', description: 'Machined hubs and an elastomer element joining the two shafts.' },
  { id: 'seal', number: 4, label: 'Mechanical seal', parent: 'pump', description: 'Seal chamber, stationary seat, rotating face and spring assembly.' },
  { id: 'impeller', number: 5, label: 'Impeller', parent: 'pump', description: 'Curved radial vanes, shroud and central impeller eye.' },
  { id: 'pump', number: 6, label: 'Pump casing and suction', parent: 'machine', description: 'Expanding volute, bolted service cover, suction elbow and support lantern.' },
  { id: 'discharge', number: 7, label: 'Discharge and valve', parent: 'pump', description: 'Tangential discharge pipe, bolted flanges and manual isolation valve.' },
  { id: 'base', number: 8, label: 'Base and anchors', parent: 'machine', description: 'Pedestal feet, base plate, mounting rails and anchor fasteners.' }
].map(part => Object.freeze({ ...part, conceptual: true })));
const PART_BY_ID = new Map(PARTS.map(part => [part.id, part]));
const Y_AXIS = new THREE.Vector3(0, 1, 0);

const MATERIALS = {
  enamel: { color: '#adbec7', metalness: .42, roughness: .4 },
  fins: { color: '#94acb7', metalness: .45, roughness: .43 },
  casting: { color: '#8ca5b2', metalness: .48, roughness: .4 },
  castingDark: { color: '#6e8995', metalness: .44, roughness: .46 },
  steel: { color: '#bac5cd', metalness: .88, roughness: .29 },
  polished: { color: '#dee4e7', metalness: .93, roughness: .2 },
  brushed: { color: '#a5b4bd', metalness: .8, roughness: .4 },
  darkSteel: { color: '#485d69', metalness: .72, roughness: .38 },
  graphite: { color: '#293a44', metalness: .24, roughness: .66 },
  rubber: { color: '#24323a', metalness: .03, roughness: .83 },
  brass: { color: '#ad925d', metalness: .75, roughness: .32 },
  bronze: { color: '#a88e66', metalness: .76, roughness: .34 },
  copper: { color: '#a56d4c', metalness: .79, roughness: .35 },
  ceramic: { color: '#e6e3da', metalness: .1, roughness: .24 },
  plate: { color: '#d8dfe3', metalness: .72, roughness: .35 }
};

function validPart(id) { return PART_BY_ID.has(id) ? id : 'machine'; }
function validMode(mode) { return MODES.has(mode) ? mode : 'assembled'; }
function vectorArray(value) { return Array.isArray(value) && value.length === 3 && value.every(Number.isFinite); }
function worldVisible(object) {
  for (let node = object; node; node = node.parent) if (!node.visible) return false;
  return true;
}

/** Solid annular/spiral sector, with true inner walls and closed cut faces. */
function annularGeometry(inner, outer, height, start = 0, sweep = TAU, segments = 96) {
  const positions = [], normals = [], indices = [];
  const innerAt = typeof inner === 'function' ? inner : () => inner;
  const outerAt = typeof outer === 'function' ? outer : () => outer;
  const point = (radius, angle, y) => [radius * Math.cos(angle), y, radius * Math.sin(angle)];
  function quad(a, b, c, d, ns) {
    const index = positions.length / 3;
    for (const p of [a, b, c, d]) positions.push(...p);
    if (Array.isArray(ns[0])) for (const n of ns) normals.push(...n);
    else for (let i = 0; i < 4; i++) normals.push(...ns);
    indices.push(index, index + 1, index + 2, index, index + 2, index + 3);
  }
  const edgeNormal = (fn, angle, sign) => {
    const epsilon = .0001, r = fn(angle), dr = (fn(angle + epsilon) - fn(angle - epsilon)) / (2 * epsilon);
    const v = new THREE.Vector3(r * Math.cos(angle) + dr * Math.sin(angle), 0, r * Math.sin(angle) - dr * Math.cos(angle)).normalize().multiplyScalar(sign);
    return v.toArray();
  };
  const half = height / 2;
  for (let i = 0; i < segments; i++) {
    const a = start + sweep * i / segments, b = start + sweep * (i + 1) / segments;
    const ia = innerAt(a), ib = innerAt(b), oa = outerAt(a), ob = outerAt(b);
    const itA = point(ia, a, half), itB = point(ib, b, half);
    const otA = point(oa, a, half), otB = point(ob, b, half);
    const ibA = point(ia, a, -half), ibB = point(ib, b, -half);
    const obA = point(oa, a, -half), obB = point(ob, b, -half);
    quad(itA, itB, otB, otA, [0, 1, 0]);
    quad(ibA, obA, obB, ibB, [0, -1, 0]);
    const noA = edgeNormal(outerAt, a, 1), noB = edgeNormal(outerAt, b, 1);
    quad(obA, otA, otB, obB, [noA, noA, noB, noB]);
    const niA = edgeNormal(innerAt, a, -1), niB = edgeNormal(innerAt, b, -1);
    quad(itA, ibA, ibB, itB, [niA, niA, niB, niB]);
  }
  if (sweep < TAU - .0001) {
    const a = start, b = start + sweep;
    quad(point(innerAt(a), a, -half), point(innerAt(a), a, half), point(outerAt(a), a, half), point(outerAt(a), a, -half), [Math.sin(a), 0, -Math.cos(a)]);
    quad(point(innerAt(b), b, -half), point(outerAt(b), b, -half), point(outerAt(b), b, half), point(innerAt(b), b, half), [-Math.sin(b), 0, Math.cos(b)]);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return geometry;
}

function bevelBoxGeometry(width, height, depth, bevel = .018) {
  const b = Math.min(bevel, width / 4, height / 4, depth / 4);
  const w = width / 2 - b, h = height / 2 - b;
  const shape = new THREE.Shape();
  shape.moveTo(-w, -h); shape.lineTo(w, -h); shape.lineTo(w, h); shape.lineTo(-w, h); shape.closePath();
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: depth - b * 2, bevelEnabled: true, bevelThickness: b, bevelSize: b, bevelSegments: 2, steps: 1, curveSegments: 1 });
  geometry.translate(0, 0, -depth / 2 + b);
  geometry.computeBoundingBox();
  return geometry;
}

function buildPumpModel(accent) {
  const selectionColor = new THREE.Color(accent);
  let selectedId = 'machine', motorAlarmColor = null, motorAlarmLit = true;
  const root = new THREE.Group();
  root.name = 'Conceptual vertical centrifugal pump';
  const geometries = new Set(), materials = new Set(), textures = new Set();
  const materialCache = new Map(), parts = new Map(), pickMeshes = [], movable = [], modeObjects = [];
  const edgeMaterial = new THREE.LineBasicMaterial({ color: selectionColor, transparent: true, opacity: .68, depthWrite: false, toneMapped: false });
  materials.add(edgeMaterial);
  const edgeCache = new Map();
  const cylinderCache = new Map(), ringCache = new Map(), boxCache = new Map(), torusCache = new Map();

  function material(id, style) {
    const key = `${id}:${style}`;
    if (!materialCache.has(key)) {
      const mat = new THREE.MeshStandardMaterial({ ...MATERIALS[style], envMapIntensity: style === 'rubber' ? .35 : .8 });
      mat.userData = { partId: id, baseColor: mat.color.clone(), baseEmissive: mat.emissive.clone() };
      materialCache.set(key, mat); materials.add(mat);
    }
    return materialCache.get(key);
  }
  function group(parent, name, position = [0, 0, 0], explodeOffset = [0, 0, 0]) {
    const result = new THREE.Group(); result.name = name; result.position.set(...position);
    result.userData.basePosition = result.position.clone(); result.userData.explodeOffset = new THREE.Vector3(...explodeOffset);
    parent.add(result); movable.push(result); return result;
  }
  function part(id, position, explodeOffset) {
    const result = group(root, PART_BY_ID.get(id).label, position, explodeOffset);
    result.userData.partId = id; parts.set(id, { group: result, meshes: [], metadata: PART_BY_ID.get(id) }); return result;
  }
  function add(parent, id, geometry, style, position = [0, 0, 0], rotation = null, outline = false) {
    geometries.add(geometry);
    const mesh = new THREE.Mesh(geometry, material(id, style));
    mesh.position.set(...position); if (rotation) mesh.rotation.set(...rotation);
    mesh.name = `${id} · ${style}`; mesh.castShadow = true; mesh.receiveShadow = true;
    mesh.userData = { partId: id, basePosition: mesh.position.clone(), baseQuaternion: mesh.quaternion.clone(), baseScale: mesh.scale.clone(), baseMaterialColor: mesh.material.color.clone(), outline };
    parent.add(mesh); pickMeshes.push(mesh); parts.get(id).meshes.push(mesh); return mesh;
  }
  function instance(parent, id, geometry, style, transforms) {
    geometries.add(geometry);
    const mesh = new THREE.InstancedMesh(geometry, material(id, style), transforms.length);
    const dummy = new THREE.Object3D();
    transforms.forEach((transform, index) => {
      dummy.position.set(...(transform.position || [0, 0, 0])); dummy.rotation.set(...(transform.rotation || [0, 0, 0])); dummy.scale.set(...(transform.scale || [1, 1, 1]));
      dummy.updateMatrix(); mesh.setMatrixAt(index, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true; mesh.computeBoundingBox(); mesh.computeBoundingSphere();
    mesh.castShadow = true; mesh.receiveShadow = true;
    mesh.userData = { partId: id, basePosition: mesh.position.clone(), baseQuaternion: mesh.quaternion.clone(), baseScale: mesh.scale.clone(), baseMaterialColor: mesh.material.color.clone(), instanceBaseTransforms: transforms };
    parent.add(mesh); pickMeshes.push(mesh); parts.get(id).meshes.push(mesh); return mesh;
  }
  function cylinder(parent, id, radius, height, style, position = [0, 0, 0], rotation = null, outline = false, bottom = radius, segments = 64) {
    const key = `${radius}:${bottom}:${height}:${segments}`;
    if (!cylinderCache.has(key)) cylinderCache.set(key, new THREE.CylinderGeometry(radius, bottom, height, segments));
    return add(parent, id, cylinderCache.get(key), style, position, rotation, outline);
  }
  function ring(parent, id, inner, outer, height, style, position = [0, 0, 0], rotation = null, start = 0, sweep = TAU, outline = false) {
    const key = `${inner}:${outer}:${height}:${start}:${sweep}`;
    if (!ringCache.has(key)) ringCache.set(key, annularGeometry(inner, outer, height, start, sweep, Math.max(20, Math.round(96 * sweep / TAU))));
    return add(parent, id, ringCache.get(key), style, position, rotation, outline);
  }
  function box(parent, id, dimensions, style, position = [0, 0, 0], rotation = null, outline = false, bevel = .018) {
    const key = `${dimensions.join(':')}:${bevel}`;
    if (!boxCache.has(key)) boxCache.set(key, bevelBoxGeometry(...dimensions, bevel));
    return add(parent, id, boxCache.get(key), style, position, rotation, outline);
  }
  function torus(parent, id, radius, tube, style, position = [0, 0, 0], rotation = [Math.PI / 2, 0, 0], outline = false) {
    const key = `${radius}:${tube}`;
    if (!torusCache.has(key)) torusCache.set(key, new THREE.TorusGeometry(radius, tube, 12, 80));
    return add(parent, id, torusCache.get(key), style, position, rotation, outline);
  }
  function tube(parent, id, points, radius, style, segments = 36) {
    const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)), false, 'centripetal');
    return add(parent, id, new THREE.TubeGeometry(curve, segments, radius, 16, false), style);
  }
  function modeObject(object, { hideCutaway = false, hideExploded = false, onlyCutaway = false } = {}) {
    modeObjects.push({ object, hideCutaway, hideExploded, onlyCutaway }); return object;
  }
  function bolts(parent, id, radius, count, center, { axis = 'y', head = .038, length = .13, phase = 0 } = {}) {
    const rotation = axis === 'x' ? [0, 0, -Math.PI / 2] : axis === 'z' ? [Math.PI / 2, 0, 0] : [0, 0, 0];
    const headTransforms = [], washerTransforms = [], shaftTransforms = [];
    for (let i = 0; i < count; i++) {
      const angle = phase + i * TAU / count;
      const radial = axis === 'x' ? [0, radius * Math.cos(angle), radius * Math.sin(angle)] : axis === 'z' ? [radius * Math.cos(angle), radius * Math.sin(angle), 0] : [radius * Math.cos(angle), 0, radius * Math.sin(angle)];
      const p = center.map((n, j) => n + radial[j]);
      const ax = axis === 'x' ? 0 : axis === 'z' ? 2 : 1;
      const hp = [...p], wp = [...p], sp = [...p]; hp[ax] += .025; wp[ax] += .004; sp[ax] -= length / 2;
      headTransforms.push({ position: hp, rotation }); washerTransforms.push({ position: wp, rotation }); shaftTransforms.push({ position: sp, rotation });
    }
    instance(parent, id, new THREE.CylinderGeometry(head, head, .045, 6), 'steel', headTransforms);
    instance(parent, id, annularGeometry(head * .39, head * 1.36, .012, 0, TAU, 24), 'brushed', washerTransforms);
    instance(parent, id, new THREE.CylinderGeometry(head * .49, head * .49, length, 12), 'darkSteel', shaftTransforms);
  }

  const base = part('base', [0, 0, 0], [0, 0, 0]);
  const pump = part('pump', [0, .86, 0], [0, 0, 0]);
  const discharge = part('discharge', [0, .86, 0], [.34, 0, 0]);
  const impeller = part('impeller', [0, .86, 0], [0, .08, 1.03]);
  const seal = part('seal', [0, 1.48, 0], [0, .22, 0]);
  const coupling = part('coupling', [0, 2.02, 0], [0, .48, 0]);
  const bearing = part('bearing', [0, 2.49, 0], [0, .77, 0]);
  const motor = part('motor', [0, 3.46, 0], [0, 1.12, 0]);

  // Machined base, separate rails and four anchored mounting feet.
  box(base, 'base', [1.96, .11, 1.58], 'brushed', [0, .18, 0], null, true, .025);
  for (const x of [-.75, .75]) box(base, 'base', [.17, .14, 1.51], 'castingDark', [x, .075, 0]);
  for (const x of [-.56, .56]) for (const z of [-.43, .43]) {
    box(base, 'base', [.25, .28, .26], 'castingDark', [x, .37, z], null, true);
    box(base, 'base', [.42, .055, .36], 'steel', [x, .242, z]);
    box(base, 'base', [.065, .29, .32], 'casting', [x, .38, z], [0, 0, x > 0 ? -.18 : .18]);
  }
  bolts(base, 'base', 1, 4, [0, .246, 0], { head: .058, length: .2, phase: Math.PI / 4 });
  // Small locating pads distinguish the plate edge from the floor.
  for (const x of [-.79, .79]) for (const z of [-.59, .59]) cylinder(base, 'base', .07, .017, 'darkSteel', [x, .012, z]);

  // Expanding spiral volute. The clear, positive-z service half can be
  // removed without transparent materials or fragile global clipping.
  const voluteOuter = a => .81 + .23 * a / TAU;
  const frontCasing = group(pump, 'Removable front volute half', [0, 0, 0], [0, .02, 1.64]);
  const rearCasing = group(pump, 'Rear volute half');
  const cover = group(pump, 'Bolted casing service cover', [0, .238, 0], [0, .24, 1.64]);
  for (const [parent, start, near] of [[frontCasing, 0, true], [rearCasing, Math.PI, false]]) {
    const shell = add(parent, 'pump', annularGeometry(.625, voluteOuter, .39, start, Math.PI, 64), 'casting', [0, 0, 0], null, true);
    if (near) modeObject(shell, { hideCutaway: true });
    const bottom = add(parent, 'pump', annularGeometry(.185, voluteOuter, .07, start, Math.PI, 64), 'castingDark', [0, -.218, 0]);
    if (near) modeObject(bottom, { hideCutaway: true });
    const top = add(cover, 'pump', annularGeometry(.20, voluteOuter, .074, start, Math.PI, 64), 'casting', [0, 0, 0], null, true);
    if (near) modeObject(top, { hideCutaway: true });
    const lip = ring(cover, 'pump', .697, .749, .022, 'steel', [0, .048, 0], null, start, Math.PI);
    if (near) modeObject(lip, { hideCutaway: true });
    const coverBolts = group(cover, near ? 'Front cover fasteners' : 'Rear cover fasteners');
    // Each half has its own fasteners, so the cut face stays uncluttered.
    const transforms = [];
    for (let i = 0; i < 6; i++) {
      const a = start + (i + .5) * Math.PI / 6;
      transforms.push({ position: [.786 * Math.cos(a), .078, .786 * Math.sin(a)], rotation: [0, a, 0] });
    }
    instance(coverBolts, 'pump', new THREE.CylinderGeometry(.041, .041, .055, 6), 'steel', transforms);
    if (near) modeObject(coverBolts, { hideCutaway: true });
  }
  // A real axial suction path turns from the left pipe into the underside of
  // the radial impeller; the discharge leaves the rear volute tangentially.
  tube(pump, 'pump', [[-2.03, -.4, 0], [-1.25, -.4, 0], [-.57, -.4, 0], [-.17, -.35, 0], [0, -.18, 0], [0, -.10, 0]], .195, 'casting', 64);
  for (const x of [-1.94, -.91]) {
    ring(pump, 'pump', .197, .333, .105, 'steel', [x, -.4, 0], [0, 0, -Math.PI / 2], 0, TAU, true);
    ring(pump, 'pump', .197, .265, .024, 'polished', [x - .063, -.4, 0], [0, 0, -Math.PI / 2]);
    bolts(pump, 'pump', .276, 8, [x + .058, -.4, 0], { axis: 'x', head: .031, length: .17, phase: Math.PI / 8 });
  }
  // Deep pipe bore at the open suction end, not a flat painted end cap.
  cylinder(pump, 'pump', .186, .012, 'graphite', [-1.81, -.4, 0], [0, 0, Math.PI / 2]);
  cylinder(pump, 'pump', .39, .33, 'casting', [0, .43, 0], null, true, .49);
  ring(pump, 'pump', .21, .45, .073, 'steel', [0, .618, 0], null, 0, TAU, true);
  for (const x of [-.51, .51]) {
    box(pump, 'pump', [.135, 1.13, .18], 'castingDark', [x, 1.11, -.27], [0, 0, x > 0 ? -.028 : .028], true);
    box(pump, 'pump', [.27, .095, .31], 'brushed', [x, 1.68, -.27]);
    box(pump, 'pump', [.24, .12, .32], 'casting', [x, .53, -.27]);
  }
  // Front drain, plug and service identification pad.
  cylinder(frontCasing, 'pump', .052, .08, 'brass', [-.25, -.14, .81], [Math.PI / 2, 0, 0], false, .052, 6);
  const pumpPlate = box(frontCasing, 'pump', [.29, .115, .021], 'plate', [.19, -.016, .884], null, false, .006);
  modeObject(pumpPlate, { hideCutaway: true });

  // Seven backward-curved vanes and a central eye, built as solid swept
  // strips. No torus stands in for the pump or impeller geometry.
  ring(impeller, 'impeller', .124, .637, .039, 'bronze', [0, -.112, 0], null, 0, TAU, true);
  ring(impeller, 'impeller', .125, .239, .24, 'steel', [0, -.002, 0], null, 0, TAU, true);
  for (let i = 0; i < 7; i++) {
    const start = i * TAU / 7 + .05;
    const inner = a => .202 + .406 * (a - start) / .81;
    const outer = a => inner(a) + .034;
    add(impeller, 'impeller', annularGeometry(inner, outer, .183, start, .81, 28), 'bronze', [0, 0, 0], null, true);
  }
  const impellerFront = ring(impeller, 'impeller', .225, .642, .027, 'bronze', [0, .106, 0], null, 0, Math.PI, true);
  const impellerRear = ring(impeller, 'impeller', .225, .642, .027, 'bronze', [0, .106, 0], null, Math.PI, Math.PI, true);
  modeObject(impellerFront, { hideCutaway: true, hideExploded: true });
  modeObject(impellerRear, { hideExploded: true });
  cylinder(impeller, 'impeller', .099, .50, 'polished', [0, .24, 0]);
  ring(impeller, 'impeller', .102, .145, .025, 'darkSteel', [0, .127, 0]);

  // Tangential discharge pipe and a restrained, recognisable hand valve.
  tube(discharge, 'discharge', [[-.02, .02, -.985], [.42, .02, -.94], [.9, .02, -.85], [1.22, .02, -.85]], .205, 'casting', 42);
  ring(discharge, 'discharge', .179, .208, 1.19, 'casting', [1.87, .02, -.85], [0, 0, Math.PI / 2], 0, TAU, true);
  for (const x of [1.13, 1.42, 2.035, 2.43]) {
    ring(discharge, 'discharge', .206, .326, .094, 'steel', [x, .02, -.85], [0, 0, -Math.PI / 2], 0, TAU, true);
    ring(discharge, 'discharge', .207, .271, .015, 'graphite', [x - .053, .02, -.85], [0, 0, -Math.PI / 2]);
    bolts(discharge, 'discharge', .272, 8, [x + .05, .02, -.85], { axis: 'x', head: .03, length: .15, phase: Math.PI / 8 });
  }
  cylinder(discharge, 'discharge', .283, .52, 'castingDark', [1.73, .02, -.85], [0, 0, Math.PI / 2], true);
  cylinder(discharge, 'discharge', .142, .32, 'casting', [1.73, .295, -.85], null, true, .245);
  ring(discharge, 'discharge', .054, .174, .055, 'steel', [1.73, .469, -.85]);
  bolts(discharge, 'discharge', .133, 4, [1.73, .5, -.85], { head: .024, length: .13, phase: Math.PI / 4 });
  cylinder(discharge, 'discharge', .037, .25, 'polished', [1.73, .622, -.85]);
  cylinder(discharge, 'discharge', .067, .07, 'darkSteel', [1.73, .748, -.85]);
  torus(discharge, 'discharge', .245, .024, 'darkSteel', [1.73, .788, -.85], [Math.PI / 2, 0, 0], true);
  const spokes = [];
  for (let i = 0; i < 6; i++) { const a = i * TAU / 6; spokes.push({ position: [1.73 + Math.cos(a) * .122, .788, -.85 + Math.sin(a) * .122], rotation: [0, -a, Math.PI / 2] }); }
  instance(discharge, 'discharge', new THREE.CylinderGeometry(.013, .013, .236, 12), 'darkSteel', spokes);
  cylinder(discharge, 'discharge', .2, .012, 'graphite', [2.3, .02, -.85], [0, 0, Math.PI / 2]);

  // Mechanical seal: separate chamber wall, face pair, bellows and springs.
  cylinder(seal, 'seal', .103, .61, 'polished', [0, 0, 0]);
  const sealNear = ring(seal, 'seal', .287, .364, .277, 'castingDark', [0, -.01, 0], null, 0, Math.PI, true);
  ring(seal, 'seal', .287, .364, .277, 'castingDark', [0, -.01, 0], null, Math.PI, Math.PI, true);
  modeObject(sealNear, { hideCutaway: true });
  ring(seal, 'seal', .106, .372, .057, 'steel', [0, .163, 0], null, 0, TAU, true);
  ring(seal, 'seal', .107, .361, .05, 'brushed', [0, -.174, 0], null, 0, TAU, true);
  ring(seal, 'seal', .108, .239, .052, 'ceramic', [0, -.096, 0]);
  ring(seal, 'seal', .108, .232, .043, 'graphite', [0, -.047, 0]);
  for (let i = 0; i < 6; i++) torus(seal, 'seal', .172, .011, 'rubber', [0, .011 + i * .017, 0]);
  for (let i = 0; i < 8; i++) {
    const a = i * TAU / 8, points = [];
    for (let j = 0; j <= 48; j++) { const t = j / 48, theta = t * TAU * 3.5; points.push([Math.cos(a) * .261 + Math.cos(theta) * .017, -.057 + t * .163, Math.sin(a) * .261 + Math.sin(theta) * .017]); }
    tube(seal, 'seal', points, .0048, 'steel', 48);
  }
  bolts(seal, 'seal', .312, 6, [0, .198, 0], { head: .025, length: .14, phase: Math.PI / 6 });
  tube(seal, 'seal', [[.33, -.015, -.05], [.47, -.015, -.05], [.52, .03, -.07], [.52, .17, -.12], [.43, .22, -.16]], .018, 'steel', 30);
  cylinder(seal, 'seal', .041, .069, 'brass', [.365, -.015, -.05], [0, 0, Math.PI / 2], false, .041, 6);

  // Jaw coupling: the dark elastomer is interleaved between machined hubs.
  cylinder(coupling, 'coupling', .102, .745, 'polished', [0, 0, 0]);
  ring(coupling, 'coupling', .105, .218, .175, 'steel', [0, -.19, 0], null, 0, TAU, true);
  ring(coupling, 'coupling', .105, .225, .175, 'brass', [0, .191, 0], null, 0, TAU, true);
  ring(coupling, 'coupling', .106, .279, .055, 'darkSteel', [0, -.081, 0], null, 0, TAU, true);
  ring(coupling, 'coupling', .106, .282, .055, 'brass', [0, .081, 0], null, 0, TAU, true);
  ring(coupling, 'coupling', .113, .273, .07, 'rubber', [0, 0, 0]);
  for (let i = 0; i < 6; i++) {
    const a = i * TAU / 6;
    ring(coupling, 'coupling', .182, .287, .113, i % 2 ? 'brass' : 'steel', [0, i % 2 ? .024 : -.024, 0], null, a + .08, .39, true);
  }
  // Socket set screws on the two hubs.
  for (const y of [-.188, .193]) {
    cylinder(coupling, 'coupling', .026, .045, 'graphite', [.221, y, 0], [0, 0, Math.PI / 2], false, .026, 6);
    cylinder(coupling, 'coupling', .026, .045, 'graphite', [0, y, .221], [Math.PI / 2, 0, 0], false, .026, 6);
  }
  // Open-backed guard frame gives a credible support context without hiding
  // the coupling or implying that a cutaway is a normal operating state.
  for (const y of [-.32, .32]) ring(coupling, 'coupling', .389, .416, .026, 'castingDark', [0, y, 0], null, Math.PI, Math.PI);
  const guardStruts = [];
  for (let i = 0; i <= 8; i++) { const a = Math.PI + i * Math.PI / 8; guardStruts.push({ position: [Math.cos(a) * .406, 0, Math.sin(a) * .406], rotation: [0, -a, 0] }); }
  instance(coupling, 'coupling', bevelBoxGeometry(.025, .65, .025, .006), 'castingDark', guardStruts);

  // Open rolling bearing. Both races are actual rings; the shaft passes
  // through the bore and the steel balls remain individually legible.
  ring(bearing, 'bearing', .343, .438, .164, 'steel', [0, 0, 0], null, 0, TAU, true);
  ring(bearing, 'bearing', .129, .222, .164, 'polished', [0, 0, 0], null, 0, TAU, true);
  torus(bearing, 'bearing', .353, .012, 'darkSteel', [0, .056, 0]);
  torus(bearing, 'bearing', .214, .01, 'darkSteel', [0, .056, 0]);
  const ballTransforms = [];
  for (let i = 0; i < 12; i++) { const a = i * TAU / 12; ballTransforms.push({ position: [.282 * Math.cos(a), 0, .282 * Math.sin(a)] }); }
  instance(bearing, 'bearing', new THREE.SphereGeometry(.063, 24, 16), 'polished', ballTransforms);
  for (const y of [-.043, .043]) {
    torus(bearing, 'bearing', .245, .006, 'brass', [0, y, 0]);
    torus(bearing, 'bearing', .32, .006, 'brass', [0, y, 0]);
  }
  const cageBridges = [];
  for (let i = 0; i < 12; i++) { const a = (i + .5) * TAU / 12; cageBridges.push({ position: [Math.cos(a) * .282, 0, Math.sin(a) * .282], rotation: [0, -a, 0] }); }
  instance(bearing, 'bearing', bevelBoxGeometry(.076, .075, .012, .004), 'brass', cageBridges);
  ring(bearing, 'bearing', .129, .206, .028, 'darkSteel', [0, -.104, 0]);

  // Motor shell and fins are partitioned along the same real sector. The
  // missing front sector in cutaway exposes a stator, copper end turns and
  // a slotted rotor rather than ghosting the entire model.
  const cutAngle = Math.PI * .68;
  const motorNear = group(motor, 'Motor removable shell sector');
  const motorRear = group(motor, 'Motor retained shell sector');
  modeObject(motorNear, { hideCutaway: true });
  for (const [parent, start, sweep] of [[motorNear, 0, cutAngle], [motorRear, cutAngle, TAU - cutAngle]]) {
    ring(parent, 'motor', .535, .594, 1.49, 'enamel', [0, .014, 0], null, start, sweep, true);
    const finTransforms = [];
    for (let i = 0; i < 32; i++) { const a = i * TAU / 32; if (a >= start - .0001 && a < start + sweep - .0001) finTransforms.push({ position: [Math.cos(a) * .624, .008, Math.sin(a) * .624], rotation: [0, -a, 0] }); }
    if (finTransforms.length) instance(parent, 'motor', bevelBoxGeometry(.151, 1.366, .029, .008), 'fins', finTransforms);
  }
  ring(motor, 'motor', .134, .704, .105, 'brushed', [0, -.822, 0], null, 0, TAU, true);
  ring(motor, 'motor', .134, .616, .095, 'enamel', [0, -.724, 0]);
  torus(motor, 'motor', .584, .024, 'steel', [0, -.662, 0]);
  ring(motor, 'motor', .14, .605, .09, 'brushed', [0, .802, 0], null, 0, TAU, true);
  cylinder(motor, 'motor', .57, .174, 'enamel', [0, .915, 0], null, true, .607);
  torus(motor, 'motor', .563, .018, 'steel', [0, 1.006, 0]);
  // Recessed radial fan grille, with visible gaps between the spokes.
  cylinder(motor, 'motor', .483, .01, 'graphite', [0, 1.007, 0]);
  for (const r of [.14, .255, .37, .484]) torus(motor, 'motor', r, .01, 'brushed', [0, 1.019, 0]);
  const fanGrille = [];
  for (let i = 0; i < 16; i++) { const a = i * TAU / 16; fanGrille.push({ position: [.26 * Math.cos(a), 1.023, .26 * Math.sin(a)], rotation: [0, -a, Math.PI / 2] }); }
  instance(motor, 'motor', new THREE.CylinderGeometry(.009, .009, .455, 8), 'brushed', fanGrille);
  cylinder(motor, 'motor', .089, .029, 'steel', [0, 1.039, 0]);
  // Offset lifting eye keeps the grille visible from the default view.
  cylinder(motor, 'motor', .052, .09, 'darkSteel', [0, 1.08, -.165]);
  torus(motor, 'motor', .10, .024, 'brushed', [0, 1.191, -.165], [0, 0, 0], true);
  bolts(motor, 'motor', .645, 8, [0, -.76, 0], { head: .037, length: .17, phase: Math.PI / 8 });

  // Laminated stator stack, with the same open sector as the outer shell.
  for (const [start, sweep, near] of [[0, cutAngle, true], [cutAngle, TAU - cutAngle, false]]) {
    const stator = group(motor, 'Laminated stator sector');
    const slices = [];
    for (let i = 0; i < 21; i++) slices.push({ position: [0, -.573 + i * .057, 0] });
    instance(stator, 'motor', annularGeometry(.415, .529, .047, start, sweep, 56), 'darkSteel', slices);
    if (near) modeObject(stator, { hideCutaway: true });
  }
  // Individual coil loops leave the internal rotor visible between slots.
  for (let i = 0; i < 12; i++) {
    const a = i * TAU / 12 + .08, c = Math.cos(a), s = Math.sin(a);
    const points = [[.43 * c, -.62, .43 * s], [.478 * c, -.68, .478 * s], [.5 * c, -.57, .5 * s], [.5 * c, .57, .5 * s], [.473 * c, .67, .473 * s], [.429 * c, .62, .429 * s], [.421 * c, .52, .421 * s], [.421 * c, -.52, .421 * s], [.43 * c, -.62, .43 * s]];
    tube(motor, 'motor', points, .021, 'copper', 58);
  }
  cylinder(motor, 'motor', .287, 1.16, 'brushed', [0, 0, 0], null, true);
  cylinder(motor, 'motor', .306, .057, 'darkSteel', [0, -.606, 0]);
  cylinder(motor, 'motor', .306, .057, 'darkSteel', [0, .606, 0]);
  cylinder(motor, 'motor', .101, 2.16, 'polished', [0, -.027, 0]);
  const rotorBars = [];
  for (let i = 0; i < 22; i++) { const a = i * TAU / 22; rotorBars.push({ position: [.288 * Math.cos(a), 0, .288 * Math.sin(a)], rotation: [0, -a, -.025] }); }
  instance(motor, 'motor', bevelBoxGeometry(.014, 1.075, .024, .004), 'darkSteel', rotorBars);

  // Side terminal box with a gasketed lid, screws, hex cable gland and a
  // deliberately static cable run. It carries no invented specifications.
  box(motor, 'motor', [.32, .47, .46], 'castingDark', [.708, .09, -.10], null, true, .028);
  box(motor, 'motor', [.045, .476, .468], 'graphite', [.884, .09, -.10], null, false, .01);
  box(motor, 'motor', [.065, .486, .478], 'enamel', [.925, .09, -.10], null, true, .017);
  const lidScrews = [];
  for (const y of [-.09, .27]) for (const z of [-.275, .075]) lidScrews.push({ position: [.963, y, z], rotation: [0, 0, Math.PI / 2] });
  instance(motor, 'motor', new THREE.CylinderGeometry(.024, .024, .012, 6), 'steel', lidScrews);
  cylinder(motor, 'motor', .066, .1, 'brushed', [1.008, -.035, -.10], [0, 0, Math.PI / 2], true, .066, 6);
  cylinder(motor, 'motor', .047, .07, 'graphite', [1.083, -.035, -.10], [0, 0, Math.PI / 2]);
  tube(motor, 'motor', [[1.12, -.035, -.10], [1.18, -.11, -.13], [1.18, -.44, -.21], [1.10, -.69, -.27], [.94, -.82, -.30]], .027, 'rubber', 44);
  // Generic, etched nameplate. It is explicitly a reference model and does
  // not invent a serial number, power rating, manufacturer or asset value.
  const nameplate = box(motorNear, 'motor', [.335, .194, .025], 'plate', [0, .04, .722], null, false, .008);
  if (typeof document !== 'undefined') {
    const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 256;
    const context = canvas.getContext('2d');
    if (context) {
      context.fillStyle = '#cbd5dc'; context.fillRect(0, 0, 512, 256);
      context.strokeStyle = '#899aa6'; context.lineWidth = 3; context.strokeRect(13, 13, 486, 230);
      context.fillStyle = '#334b5c'; context.font = '600 55px Arial, sans-serif'; context.fillText('EQUA', 38, 84);
      context.font = '500 23px Arial, sans-serif'; context.fillText('EQUIPMENT REFERENCE', 40, 126);
      context.fillStyle = '#8093a0';
      for (let row = 0; row < 3; row++) { context.fillRect(40, 150 + row * 24, 108, 6); context.fillRect(172, 150 + row * 24, row === 1 ? 233 : 269, 6); }
      const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 4; textures.add(texture);
      const plateMaterial = new THREE.MeshStandardMaterial({ map: texture, metalness: .46, roughness: .45, color: '#ffffff' });
      plateMaterial.userData = { partId: 'motor', baseColor: plateMaterial.color.clone(), baseEmissive: plateMaterial.emissive.clone() }; materials.add(plateMaterial); materialCache.set('motor:nameplate', plateMaterial);
      const front = add(motorNear, 'motor', new THREE.PlaneGeometry(.313, .173), 'plate', [0, .04, .736]); front.material = plateMaterial; front.castShadow = false;
    }
  }
  const plateScrews = [];
  for (const x of [-.145, .145]) for (const y of [-.028, .109]) plateScrews.push({ position: [x, y, .738], rotation: [Math.PI / 2, 0, 0] });
  instance(motorNear, 'motor', new THREE.CylinderGeometry(.011, .011, .008, 12), 'darkSteel', plateScrews);
  nameplate.userData.referenceOnly = true;

  const markerDefinitions = [
    { id: 'motor', p: [-.36, .34, .68], offset: [-22, -8] },
    { id: 'bearing', p: [.355, .035, .247], offset: [23, 0] },
    { id: 'coupling', p: [-.185, .035, .257], offset: [-24, -1] },
    { id: 'seal', p: [.316, .044, .18], offset: [25, 5] },
    { id: 'impeller', p: [.40, .115, .405], offset: [-19, 19] },
    { id: 'pump', p: [-.538, -.014, .714], offset: [-24, 5] },
    { id: 'discharge', p: [1.754, .43, -.69], offset: [25, -9] },
    { id: 'base', p: [-.81, .238, .62], offset: [-20, 14] }
  ];
  const markers = markerDefinitions.map(marker => ({ ...marker, point: new THREE.Vector3(...marker.p), ...PART_BY_ID.get(marker.id) }));

  function applyMode(mode) {
    for (const item of movable) item.position.copy(item.userData.basePosition).addScaledVector(item.userData.explodeOffset, mode === 'exploded' ? 1 : 0);
    for (const item of modeObjects) item.object.visible = !(item.hideCutaway && mode === 'cutaway') && !(item.hideExploded && mode === 'exploded') && (!item.onlyCutaway || mode === 'cutaway');
    root.updateMatrixWorld(true);
  }
  function select(id) {
    selectedId = id;
    for (const mat of materialCache.values()) {
      const selected = id !== 'machine' && mat.userData.partId === id;
      mat.color.copy(mat.userData.baseColor);
      if (selected) mat.color.lerp(selectionColor, .22);
      mat.emissive.copy(mat.userData.baseEmissive); mat.emissiveIntensity = 1;
      if (selected) { mat.emissive.copy(selectionColor); mat.emissiveIntensity = .095; }
      if (mat.userData.partId === 'motor' && motorAlarmColor) {
        mat.color.lerp(motorAlarmColor, motorAlarmLit ? .82 : .24);
        mat.emissive.copy(motorAlarmColor); mat.emissiveIntensity = motorAlarmLit ? .3 : .035;
      }
    }
    for (const mesh of pickMeshes) {
      if (!mesh.userData.outline) continue;
      const selected = id !== 'machine' && mesh.userData.partId === id;
      if (selected && !mesh.userData.selectionEdge) {
        if (!edgeCache.has(mesh.geometry)) { const geometry = new THREE.EdgesGeometry(mesh.geometry, 32); edgeCache.set(mesh.geometry, geometry); geometries.add(geometry); }
        const edge = new THREE.LineSegments(edgeCache.get(mesh.geometry), edgeMaterial); edge.renderOrder = 2; edge.userData.annotation = true; mesh.add(edge); mesh.userData.selectionEdge = edge;
      }
      if (mesh.userData.selectionEdge) mesh.userData.selectionEdge.visible = selected;
    }
  }
  function bounds(id = 'machine') {
    root.updateMatrixWorld(true);
    const result = new THREE.Box3();
    const list = id === 'machine' ? pickMeshes : parts.get(id)?.meshes || [];
    for (const mesh of list) {
      if (!worldVisible(mesh)) continue;
      let box;
      if (mesh.isInstancedMesh) { if (!mesh.boundingBox) mesh.computeBoundingBox(); box = mesh.boundingBox; }
      else { if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox(); box = mesh.geometry.boundingBox; }
      if (box && !box.isEmpty()) result.union(box.clone().applyMatrix4(mesh.matrixWorld));
    }
    return result;
  }
  function dispose() {
    for (const geometry of geometries) geometry.dispose();
    for (const mat of materials) mat.dispose();
    for (const texture of textures) texture.dispose();
    root.clear(); parts.clear(); pickMeshes.length = 0;
  }
  applyMode('assembled');
  return { root, parts, pickMeshes, markers, materials, geometries, textures, applyMode, select, setMotorAlarm(color, lit) { motorAlarmColor = color; motorAlarmLit = lit; select(selectedId); }, setAccent(value) { selectionColor.set(value); edgeMaterial.color.copy(selectionColor); }, bounds, dispose };
}

function fallbackElement(stage, message) {
  const element = document.createElement('div'); element.className = 'aimms-twin-fallback'; element.setAttribute('role', 'status');
  Object.assign(element.style, { position: 'absolute', inset: '0', display: 'grid', placeContent: 'center', gap: '8px', padding: '30px', textAlign: 'center', color: 'var(--mantine-color-text)', background: 'var(--mantine-color-body)', font: 'inherit', zIndex: '6' });
  const title = document.createElement('strong'); title.textContent = '3D equipment view unavailable'; title.style.fontSize = '15px';
  const detail = document.createElement('span'); detail.textContent = message; detail.style.maxWidth = '320px';
  element.append(title, detail); stage.append(element); return element;
}

function create(host, options = {}) {
  if (!host || typeof host.appendChild !== 'function') throw new TypeError('AIMMS Twin requires a host element.');
  let selected = validPart(options.selected || 'machine'), mode = validMode(options.mode), labels = options.labels !== false;
  let destroyed = false, ready = false, contextLost = false, scheduled = 0, width = 0, height = 0, lastProjected = [], failure = null;
  let renderer, controls, resizeObserver, windowResize, model, environmentTarget, scene, camera;
  let motorAlarm = options.motorAlarm || null, alarmLit = true, alarmTimer = null;
  const cleanupListeners = [];
  const externalGeometries = new Set(), externalMaterials = new Set(), externalTextures = new Set();
  const originalPosition = host.style.position;
  const positionChanged = typeof getComputedStyle === 'function' && getComputedStyle(host).position === 'static';
  if (positionChanged) host.style.position = 'relative';
  const stage = document.createElement('div'); stage.className = 'aimms-twin-scene';
  Object.assign(stage.style, { position: 'absolute', inset: '0', overflow: 'hidden', isolation: 'isolate', background: 'var(--mantine-color-body, #f4f6fa)', color: 'var(--mantine-primary-color-filled)' });
  host.appendChild(stage);
  const surface = () => getComputedStyle(stage).backgroundColor;
  const reportError = error => { if (typeof options.onError === 'function') options.onError(error); };
  const emptyState = () => ({ ready: false, selected, mode, labels, conceptual: true, cameraState: null, camera: null, error: failure?.message || null });

  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'low-power', preserveDrawingBuffer: false, stencil: false });
    renderer.setPixelRatio(Math.min(Math.max(window.devicePixelRatio || 1, 1), 1.75));
    renderer.setClearColor(surface(), 1);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.14;
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap;
    const canvas = renderer.domElement; canvas.className = 'aimms-twin-canvas'; canvas.tabIndex = 0;
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-roledescription', 'interactive 3D equipment model');
    canvas.setAttribute('aria-label', 'Conceptual vertical pump train. Drag to rotate. Scroll or use plus and minus to zoom. Arrow keys rotate. R resets the view. Select numbered components or use the component list.');
    Object.assign(canvas.style, { display: 'block', width: '100%', height: '100%', touchAction: 'none', outlineOffset: '-4px' }); stage.append(canvas);
    const style = document.createElement('style');
    style.textContent = '.aimms-twin-canvas:focus-visible{outline:2px solid var(--mantine-color-text);box-shadow:0 0 0 4px var(--mantine-primary-color-7)}.aimms-twin-hotspot:focus-visible{outline:2px solid var(--mantine-color-text);box-shadow:0 0 0 4px var(--mantine-primary-color-7);outline-offset:3px}.aimms-twin-hotspot:hover{box-shadow:0 0 0 4px var(--mantine-primary-color-light),0 2px 6px #1c35551f!important}.aimms-twin-hotspot[aria-pressed="true"]{background:var(--mantine-primary-color-8)!important;color:var(--mantine-primary-color-contrast)!important;border-color:var(--mantine-color-text)!important}.aimms-twin-hotspot[hidden]{display:none!important}';
    stage.append(style);
    scene = new THREE.Scene(); scene.background = new THREE.Color(surface());
    camera = new THREE.PerspectiveCamera(34, 1, .05, 70);
    controls = new OrbitControls(camera, canvas); controls.enableDamping = false; controls.enablePan = true; controls.screenSpacePanning = true;
    controls.minDistance = 1.8; controls.maxDistance = 23; controls.minPolarAngle = .045; controls.maxPolarAngle = Math.PI * .535;
    controls.rotateSpeed = .66; controls.zoomSpeed = .8; controls.panSpeed = .7; controls.autoRotate = false;
    const hemi = new THREE.HemisphereLight('#ffffff', '#b2bfce', 2.25); scene.add(hemi);
    const key = new THREE.DirectionalLight('#fff9f1', 2.8); key.position.set(-3.8, 8.5, 5.8); key.target.position.set(0, 2, 0); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048); key.shadow.camera.left = -5.4; key.shadow.camera.right = 5.4; key.shadow.camera.top = 7.2; key.shadow.camera.bottom = -4.7;
    key.shadow.camera.near = .5; key.shadow.camera.far = 22; key.shadow.normalBias = .018; key.shadow.bias = -.00015; key.shadow.radius = 3;
    scene.add(key, key.target);
    const fill = new THREE.DirectionalLight('#edf3ff', 1.25); fill.position.set(4.8, 5.5, -4.1); scene.add(fill);
    // A locally authored studio reflection environment gives steel a real
    // machined finish without downloading HDRs or using cinematic effects.
    function makeEnvironment() {
      const room = new RoomEnvironment(), generator = new THREE.PMREMGenerator(renderer);
      try {
        const next = generator.fromScene(room, .04, .1, 80, { size: 128 });
        environmentTarget?.dispose(); environmentTarget = next;
        scene.environment = environmentTarget.texture; scene.environmentIntensity = .66;
      } catch { /* Direct diffuse lights still provide a usable rendering. */ }
      finally { room.dispose(); generator.dispose(); }
    }
    makeEnvironment();

    model = buildPumpModel(getComputedStyle(stage).color); scene.add(model.root); model.applyMode(mode); model.select(selected);
    const floorGeometry = new THREE.PlaneGeometry(32, 32); externalGeometries.add(floorGeometry);
    const floorMaterial = new THREE.MeshBasicMaterial({ color: surface(), toneMapped: false }); externalMaterials.add(floorMaterial);
    const floor = new THREE.Mesh(floorGeometry, floorMaterial); floor.rotation.x = -Math.PI / 2; floor.position.y = -.008; floor.receiveShadow = true; scene.add(floor);
    const grid = new THREE.GridHelper(15, 30, getComputedStyle(stage).getPropertyValue('--mantine-color-default-border').trim(), getComputedStyle(stage).getPropertyValue('--mantine-color-default-border').trim()); grid.position.y = -.004; grid.material.transparent = true; grid.material.opacity = .28; grid.material.depthWrite = false;
    externalGeometries.add(grid.geometry); externalMaterials.add(grid.material); scene.add(grid);
    // A broad, soft contact patch makes the mounting feet read against the
    // floor even on implementations with coarse shadow filtering.
    const shadowCanvas = document.createElement('canvas'); shadowCanvas.width = shadowCanvas.height = 128;
    const shadowContext = shadowCanvas.getContext('2d');
    if (shadowContext) {
      const gradient = shadowContext.createRadialGradient(64, 64, 5, 64, 64, 64); gradient.addColorStop(0, 'rgba(39,57,80,.2)'); gradient.addColorStop(.45, 'rgba(39,57,80,.105)'); gradient.addColorStop(1, 'rgba(39,57,80,0)');
      shadowContext.fillStyle = gradient; shadowContext.fillRect(0, 0, 128, 128);
      const shadowTexture = new THREE.CanvasTexture(shadowCanvas); externalTextures.add(shadowTexture);
      const geometry = new THREE.PlaneGeometry(4.3, 2.8), material = new THREE.MeshBasicMaterial({ map: shadowTexture, transparent: true, depthWrite: false, toneMapped: false }); externalGeometries.add(geometry); externalMaterials.add(material);
      const contact = new THREE.Mesh(geometry, material); contact.rotation.x = -Math.PI / 2; contact.position.set(.13, -.001, -.12); scene.add(contact);
    }

    const layer = document.createElement('div'); layer.className = 'aimms-twin-hotspots'; layer.setAttribute('aria-label', 'Equipment components');
    Object.assign(layer.style, { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '3' }); stage.append(layer);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('aria-hidden', 'true');
    Object.assign(svg.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', overflow: 'visible', pointerEvents: 'none' }); layer.append(svg);
    const hotspotNodes = new Map();
    for (const marker of model.markers) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'aimms-twin-hotspot'; button.textContent = String(marker.number);
      button.setAttribute('aria-label', `${marker.number}. Select ${marker.label.toLowerCase()}`); button.title = marker.label; button.setAttribute('aria-pressed', String(selected === marker.id));
      Object.assign(button.style, { position: 'absolute', transform: 'translate(-50%,-50%)', width: '25px', height: '25px', borderRadius: '50%', border: '2px solid var(--mantine-color-text)', background: 'var(--mantine-color-body)', color: 'var(--mantine-color-text)', fontFamily: 'inherit', fontWeight: '600', fontSize: '11px', display: 'grid', placeItems: 'center', boxShadow: '0 1px 5px #1c355514', cursor: 'pointer', pointerEvents: 'auto', padding: '0' });
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line'); line.setAttribute('stroke-width', '1'); line.setAttribute('stroke', '#92a1b9'); line.setAttribute('opacity', '.55'); svg.append(line);
      const click = event => { event.stopPropagation(); choose(marker.id); }; button.addEventListener('click', click); cleanupListeners.push(() => button.removeEventListener('click', click));
      layer.append(button); hotspotNodes.set(marker.id, { button, line });
    }
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const alarmProbe = document.createElement('span');
    alarmProbe.hidden = true; alarmProbe.style.color = 'var(--aimms-critical, light-dark(var(--mantine-color-red-9), var(--mantine-color-red-5)))'; stage.append(alarmProbe);
    function applyMotorAlarm() {
      const node = hotspotNodes.get('motor').button;
      const active = motorAlarm?.severity === 'critical' || motorAlarm?.severity === 'warning';
      node.dataset.alarmSeverity = active ? motorAlarm.severity : '';
      node.dataset.alarmAttention = active ? motorAlarm.attention || '' : '';
      node.dataset.alarmLit = String(active && alarmLit);
      const marker = PART_BY_ID.get('motor');
      node.setAttribute('aria-label', `${marker.number}. Select ${marker.label.toLowerCase()}${active && motorAlarm.label ? ` · ${motorAlarm.label}` : ''}`);
      const color = active ? new THREE.Color(getComputedStyle(alarmProbe).color) : null;
      model.setMotorAlarm(color, alarmLit);
      node.style.borderColor = active ? getComputedStyle(alarmProbe).color : 'var(--mantine-color-text)';
      node.style.boxShadow = active ? `0 0 0 ${alarmLit ? 4 : 1}px ${getComputedStyle(alarmProbe).color}` : '0 1px 5px #1c355514';
      invalidate();
    }
    function syncMotorAlarm() {
      if (alarmTimer !== null) clearTimeout(alarmTimer); alarmTimer = null;
      alarmLit = true; applyMotorAlarm();
      function blink() {
        if (destroyed || contextLost || document.hidden) return;
        alarmLit = !alarmLit; applyMotorAlarm(); alarmTimer = setTimeout(blink, 500);
      }
      if (!reducedMotion.matches && !document.hidden && motorAlarm && motorAlarm.attention === 'unacknowledged') alarmTimer = setTimeout(blink, 500);
    }
    reducedMotion.addEventListener('change', syncMotorAlarm);
    document.addEventListener('visibilitychange', syncMotorAlarm);
    cleanupListeners.push(() => { if (alarmTimer !== null) clearTimeout(alarmTimer); reducedMotion.removeEventListener('change', syncMotorAlarm); document.removeEventListener('visibilitychange', syncMotorAlarm); });
    function setTheme() {
      scene.background.set(surface()); floorMaterial.color.set(surface());
      model.setAccent(getComputedStyle(stage).color); model.select(selected);
      const edge = new THREE.Color(getComputedStyle(stage).getPropertyValue('--mantine-color-default-border').trim());
      const colors = grid.geometry.getAttribute('color');
      for (let i = 0; i < colors.count; i++) colors.setXYZ(i, edge.r, edge.g, edge.b);
      colors.needsUpdate = true; applyMotorAlarm(); invalidate();
    }
    const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(), scratch = new THREE.Vector3();
    const cameraDirection = new THREE.Vector3(), screenDirection = new THREE.Vector3();

    function cameraState() { return { position: camera.position.toArray(), target: controls.target.toArray() }; }
    function state() { return { ready: ready && !contextLost && !destroyed, selected, mode, labels, conceptual: true, cameraState: cameraState(), camera: cameraState(), width, height, error: failure?.message || null }; }
    function visibleMeshes() { return model.pickMeshes.filter(worldVisible); }
    function projection() {
      const candidates = visibleMeshes(), placed = [], projected = [];
      camera.getWorldDirection(cameraDirection);
      for (const marker of model.markers) {
        const part = model.parts.get(marker.id);
        scratch.copy(marker.point).applyMatrix4(part.group.matrixWorld);
        const worldPoint = scratch.clone(), v = worldPoint.clone().project(camera);
        screenDirection.copy(worldPoint).sub(camera.position);
        let visible = screenDirection.dot(cameraDirection) > 0 && v.z > -1 && v.z < 1 && Math.abs(v.x) < .96 && Math.abs(v.y) < .96;
        let occluded = false;
        if (visible) {
          const distance = screenDirection.length(); raycaster.set(camera.position, screenDirection.normalize()); raycaster.far = distance;
          const first = raycaster.intersectObjects(candidates, false)[0];
          occluded = Boolean(first && first.distance < distance - .055 && first.object.userData.partId !== marker.id);
          visible = !occluded;
        }
        const anchorX = (v.x + 1) * width / 2, anchorY = (1 - v.y) * height / 2;
        let x = anchorX + marker.offset[0], y = anchorY + marker.offset[1];
        x = Math.max(20, Math.min(width - 20, x)); y = Math.max(20, Math.min(height - 20, y));
        if (visible) {
          // Greedy vertical separation keeps neighbouring bearing/coupling
          // labels usable when the complete train is viewed at a small size.
          for (let pass = 0; pass < 4; pass++) for (const previous of placed) {
            if (Math.hypot(x - previous.x, y - previous.y) < 31) y = Math.max(20, Math.min(height - 20, y + (y >= previous.y ? 1 : -1) * 25));
          }
          placed.push({ x, y });
        }
        const item = { id: marker.id, number: marker.number, n: marker.number, label: marker.label, x, y, anchorX, anchorY, visible, occluded, selected: selected === marker.id, world: worldPoint.toArray() };
        projected.push(item);
        const node = hotspotNodes.get(marker.id), show = visible && labels && options.hotspots !== false && !contextLost;
        node.button.hidden = !show; node.button.style.left = `${x}px`; node.button.style.top = `${y}px`; node.button.setAttribute('aria-pressed', String(item.selected));
        node.line.style.display = show ? '' : 'none'; node.line.setAttribute('x1', String(anchorX)); node.line.setAttribute('y1', String(anchorY)); node.line.setAttribute('x2', String(x)); node.line.setAttribute('y2', String(y)); node.line.setAttribute('stroke', item.selected ? 'var(--mantine-primary-color-filled)' : 'var(--mantine-color-text)');
      }
      raycaster.far = Infinity; lastProjected = projected;
      if (typeof options.onProject === 'function') options.onProject(projected.map(item => ({ ...item, world: [...item.world] })), { width, height, mode, selected });
    }
    function render() {
      scheduled = 0; if (destroyed || contextLost || width <= 0 || height <= 0) return;
      model.root.updateMatrixWorld(true); camera.updateMatrixWorld(true);
      try { renderer.render(scene, camera); }
      catch (cause) { fail(cause, 'RENDER_FAILED'); return; }
      projection();
      options.onOccluded?.(lastProjected.filter(item => item.occluded).map(({id, number, label}) => ({id, number, label})));
      if (!ready) {
        ready = true; failure = null;
        if (typeof options.onReady === 'function') options.onReady({ version: VERSION, renderer: `Three.js r${THREE.REVISION}`, conceptual: true, parts: PARTS, mode, selected, cameraState: cameraState(), meshCount: model.pickMeshes.length, bounds: { min: model.bounds().min.toArray(), max: model.bounds().max.toArray() } });
      }
    }
    function invalidate() { if (!scheduled && !destroyed && !contextLost) scheduled = requestAnimationFrame(render); }
    function fail(cause, code) {
      if (destroyed) return;
      const error = cause instanceof Error ? cause : new Error(String(cause)); error.code = code;
      failure = error; ready = false; contextLost = true;
      if (scheduled) cancelAnimationFrame(scheduled); scheduled = 0; layer.style.visibility = 'hidden';
      if (!stage.querySelector('.aimms-twin-fallback')) fallbackElement(stage, code === 'CONTEXT_LOST' ? 'The graphics context was interrupted. The component list remains available.' : 'This browser could not render the conceptual model. The component list remains available.');
      reportError(error);
    }
    function choose(id) { selected = validPart(id); model.select(selected); invalidate(); if (typeof options.onSelect === 'function') options.onSelect(selected, PART_BY_ID.get(selected)); }
    function fit(direction, padding = 1.13, id = 'machine') {
      const box = model.bounds(id); if (box.isEmpty()) return;
      const center = box.getCenter(new THREE.Vector3()), dir = direction.clone().normalize();
      const right = new THREE.Vector3().crossVectors(Y_AXIS, dir).normalize();
      if (right.lengthSq() < .001) right.set(1, 0, 0);
      const up = new THREE.Vector3().crossVectors(dir, right).normalize();
      const vtan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)), htan = vtan * camera.aspect;
      let distance = controls.minDistance;
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
        const corner = new THREE.Vector3(x, y, z).sub(center), depth = corner.dot(dir);
        distance = Math.max(distance, depth + Math.abs(corner.dot(up)) / vtan, depth + Math.abs(corner.dot(right)) / Math.max(.1, htan));
      }
      distance = THREE.MathUtils.clamp(distance * padding, controls.minDistance, controls.maxDistance);
      controls.target.copy(center); camera.position.copy(center).addScaledVector(dir, distance); controls.update(); invalidate();
    }
    function setView(view) {
      const directions = { iso: [1.05, .49, 1.46], front: [0, .105, 1], side: [1, .105, .02], top: [.001, 1, .001] };
      fit(new THREE.Vector3(...(directions[view] || directions.iso)));
    }
    function zoom(factor) {
      if (!Number.isFinite(factor) || factor <= 0) return;
      const offset = camera.position.clone().sub(controls.target); offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
      camera.position.copy(controls.target).add(offset); controls.update(); invalidate();
    }
    function focus(id = selected) { const partId = validPart(id); fit(camera.position.clone().sub(controls.target), partId === 'machine' ? 1.13 : 1.5, partId); }
    function resize() {
      if (destroyed) return;
      const rect = stage.getBoundingClientRect(), nextWidth = Math.round(rect.width), nextHeight = Math.round(rect.height);
      if (!nextWidth || !nextHeight || (nextWidth === width && nextHeight === height)) return;
      const wasSized = width > 0 && height > 0; width = nextWidth; height = nextHeight;
      renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
      // Only the initial fit uses a fixed direction. Resizing preserves a
      // user's target and camera distance; setView/reset can fit it again.
      if (!wasSized && !(vectorArray(options.cameraState?.position) && vectorArray(options.cameraState?.target))) setView('iso');
      invalidate();
    }

    let pointerDown = null;
    const down = event => { if (event.button === 0) pointerDown = { x: event.clientX, y: event.clientY, time: performance.now() }; };
    const up = event => {
      const start = pointerDown; pointerDown = null;
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5 || performance.now() - start.time > 700) return;
      const rect = canvas.getBoundingClientRect(); if (!rect.width || !rect.height) return;
      pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
      raycaster.far = Infinity; raycaster.setFromCamera(pointer, camera);
      const first = raycaster.intersectObjects(visibleMeshes(), false)[0];
      if (first?.object.userData.partId) choose(first.object.userData.partId);
    };
    const cancel = () => { pointerDown = null; };
    const keydown = event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
      let orbit = true;
      if (event.key === 'ArrowLeft') spherical.theta -= .105;
      else if (event.key === 'ArrowRight') spherical.theta += .105;
      else if (event.key === 'ArrowUp') spherical.phi -= .085;
      else if (event.key === 'ArrowDown') spherical.phi += .085;
      else { orbit = false; if (event.key === '+' || event.key === '=') zoom(.87); else if (event.key === '-' || event.key === '_') zoom(1.15); else if (event.key.toLowerCase() === 'r' || event.key === 'Home') setView('iso'); else if (event.key.toLowerCase() === 'f') focus(); else return; }
      event.preventDefault();
      if (orbit) { spherical.phi = THREE.MathUtils.clamp(spherical.phi, controls.minPolarAngle, controls.maxPolarAngle); camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical)); controls.update(); invalidate(); }
    };
    const lost = event => { event.preventDefault(); fail(new Error('The WebGL graphics context was lost.'), 'CONTEXT_LOST'); };
    const restored = () => { if (destroyed) return; contextLost = false; ready = false; failure = null; makeEnvironment(); stage.querySelector('.aimms-twin-fallback')?.remove(); layer.style.visibility = ''; syncMotorAlarm(); invalidate(); };
    for (const [event, listener] of [['pointerdown', down], ['pointerup', up], ['pointercancel', cancel], ['keydown', keydown], ['webglcontextlost', lost], ['webglcontextrestored', restored]]) {
      canvas.addEventListener(event, listener); cleanupListeners.push(() => canvas.removeEventListener(event, listener));
    }
    controls.addEventListener('change', invalidate); cleanupListeners.push(() => controls.removeEventListener('change', invalidate));
    if (typeof ResizeObserver !== 'undefined') { resizeObserver = new ResizeObserver(resize); resizeObserver.observe(stage); }
    else { windowResize = resize; window.addEventListener('resize', windowResize); }

    // Initial camera state is restored after controls exist and before the
    // first render, so reopening the inspector does not jump the view.
    if (vectorArray(options.cameraState?.position) && vectorArray(options.cameraState?.target)) {
      camera.position.fromArray(options.cameraState.position); controls.target.fromArray(options.cameraState.target); controls.update();
    } else { camera.position.set(7, 5.5, 9); controls.target.set(.15, 2.35, -.1); controls.update(); }
    resize();
    syncMotorAlarm();

    function destroy() {
      if (destroyed) return; destroyed = true; ready = false; lastProjected = []; if (scheduled) cancelAnimationFrame(scheduled); scheduled = 0;
      resizeObserver?.disconnect(); if (windowResize) window.removeEventListener('resize', windowResize);
      for (const cleanup of cleanupListeners) cleanup(); controls.dispose();
      model.dispose(); for (const geometry of externalGeometries) geometry.dispose(); for (const material of externalMaterials) material.dispose(); for (const texture of externalTextures) texture.dispose();
      environmentTarget?.dispose(); key.shadow?.dispose(); renderer.renderLists.dispose(); renderer.dispose(); renderer.forceContextLoss(); scene.clear(); stage.remove();
      if (positionChanged && host.style.position === 'relative') host.style.position = originalPosition;
    }
    return {
      destroy,
      setTheme,
      setRotate(value) { controls.enableRotate = Boolean(value); controls.enablePan = Boolean(value); canvas.style.touchAction = value ? 'none' : 'pan-y'; },
      select(id) { if (destroyed) return; selected = validPart(id); model.select(selected); invalidate(); },
      setMode(nextMode) { if (destroyed) return; const next = validMode(nextMode); if (next === mode) return; mode = next; model.applyMode(mode); fit(camera.position.clone().sub(controls.target)); },
      setLabels(value) { if (destroyed) return; labels = Boolean(value); invalidate(); },
      setMotorAlarm(value) { if (destroyed) return; motorAlarm = value; syncMotorAlarm(); },
      setView(view) { if (!destroyed) setView(view); },
      zoom(factor) { if (!destroyed) zoom(factor); },
      focus(id) { if (!destroyed) focus(id); },
      reset() { if (!destroyed) setView('iso'); },
      getState: state,
      getProjectedParts() { return lastProjected.map(item => ({ ...item, world: [...item.world] })); },
      parts: PARTS,
      metadata: Object.freeze({ conceptual: true, renderer: 'Three.js', version: VERSION, parts: PARTS })
    };
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error(String(cause)); error.code = 'INITIALIZATION_FAILED'; failure = error;
    if (scheduled) cancelAnimationFrame(scheduled); scheduled = 0;
    resizeObserver?.disconnect(); if (windowResize) window.removeEventListener('resize', windowResize);
    for (const cleanup of cleanupListeners) cleanup(); controls?.dispose(); model?.dispose(); environmentTarget?.dispose();
    for (const geometry of externalGeometries) geometry.dispose(); for (const material of externalMaterials) material.dispose(); for (const texture of externalTextures) texture.dispose();
    renderer?.dispose(); stage.replaceChildren(); fallbackElement(stage, 'This browser could not start the 3D renderer. The component list remains available.');
    reportError(error);
    return {
      destroy() { if (destroyed) return; destroyed = true; stage.remove(); if (positionChanged && host.style.position === 'relative') host.style.position = originalPosition; },
      select(id) { selected = validPart(id); }, setMode(value) { mode = validMode(value); }, setLabels(value) { labels = Boolean(value); }, setMotorAlarm() {}, setView() {}, zoom() {}, focus() {}, reset() {},
      getState: emptyState, getProjectedParts: () => [], parts: PARTS,
      metadata: Object.freeze({ conceptual: true, renderer: 'unavailable', version: VERSION, parts: PARTS })
    };
  }
}

export { create, PARTS, VERSION };
