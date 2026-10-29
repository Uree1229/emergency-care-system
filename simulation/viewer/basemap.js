/* Mercator geometry shared by the replay overlays and the visible map tiles.
 * No allocation, timing or simulation state is calculated here. */
(function (root) {
  'use strict';
  const PI = Math.PI, LIMIT = 85.05112878;
  const mercator = ([lat, lon]) => [lon * PI / 180, Math.log(Math.tan(PI / 4 + Math.max(-LIMIT, Math.min(LIMIT, lat)) * PI / 360))];
  function createProjection(points, synthetic = false) {
    const coords = points.filter(p => p?.length >= 2 && p.every(Number.isFinite));
    if (!coords.length) coords.push([37, 127]);
    const xy = coords.map(mercator);
    let minX = Math.min(...xy.map(p => p[0])), maxX = Math.max(...xy.map(p => p[0]));
    let minY = Math.min(...xy.map(p => p[1])), maxY = Math.max(...xy.map(p => p[1]));
    if (synthetic) {
      const padding = Math.max(Math.max(maxX - minX, maxY - minY) * .07, 1e-6);
      minX -= padding; maxX += padding; minY -= padding; maxY += padding;
    } else {
      if (maxX - minX < .002) { minX -= .002; maxX += .002; }
      if (maxY - minY < .002) { minY -= .002; maxY += .002; }
    }
    const factor = Math.min(850 / (maxX - minX), 580 / (maxY - minY));
    const centerX = (minX + maxX) / 2, centerY = (minY + maxY) / 2;
    return {
      factor, centerX, centerY,
      middleLat: coords.reduce((sum, p) => sum + p[0], 0) / coords.length,
      project(point) { const [x, y] = mercator(point); return [500 + (x - centerX) * factor, 360 - (y - centerY) * factor]; },
      unproject([x, y]) { return [(2 * Math.atan(Math.exp(centerY + (360 - y) / factor)) - PI / 2) * 180 / PI, (centerX + (x - 500) / factor) * 180 / PI]; }
    };
  }
  // Include the SVG's letterbox area, which is visible inside the map shell.
  function visibleSvgBounds(width, height) {
    const scale = Math.max(.01, Math.min(width / 1000, height / 720));
    return { left: (1000 - width / scale) / 2, top: (720 - height / scale) / 2, width: width / scale, height: height / scale, scale };
  }
  function visibleTiles(projection, viewport, zoom = 1, pan = [0, 0]) {
    const z = Math.max(2, Math.min(18, Math.floor(Math.log2(projection.factor * 2 * PI * zoom * viewport.scale / 256))));
    const n = 2 ** z;
    const local = ([x, y]) => [(x - pan[0]) / zoom, (y - pan[1]) / zoom];
    const [left, top] = local([viewport.left, viewport.top]);
    const [right, bottom] = local([viewport.left + viewport.width, viewport.top + viewport.height]);
    const world = ([x, y]) => [(projection.centerX + (x - 500) / projection.factor + PI) / (2 * PI) * n, (PI - projection.centerY - (360 - y) / projection.factor) / (2 * PI) * n];
    const a = world([left, top]), b = world([right, bottom]);
    const tiles = [], size = 2 * PI / n * projection.factor;
    for (let y = Math.max(0, Math.floor(a[1])); y <= Math.min(n - 1, Math.floor(b[1] - 1e-9)); y++) {
      for (let x = Math.floor(a[0]); x <= Math.floor(b[0] - 1e-9); x++) {
        tiles.push({ key: `${z}/${x}/${y}`, z, x: ((x % n) + n) % n, y,
          left: 500 + (x / n * 2 * PI - PI - projection.centerX) * projection.factor,
          top: 360 - (PI - y / n * 2 * PI - projection.centerY) * projection.factor,
          size });
      }
    }
    return tiles;
  }
  const api = { mercator, createProjection, visibleSvgBounds, visibleTiles };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ReplayBasemap = api;
})(typeof window !== 'undefined' ? window : globalThis);
