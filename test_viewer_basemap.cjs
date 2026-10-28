const assert = require('node:assert/strict');
const {test} = require('node:test');
const {createProjection, visibleSvgBounds, visibleTiles} = require('./simulation/viewer/basemap.js');
const p=createProjection([[37.1,126.9],[37.3,127.2]]);
const viewport=visibleSvgBounds(1000,550);
test('Projection round-trip preserves coordinates for routes, markers and tiles',()=>{
 for(const point of [[37.17,127.04],[37.27,127.19],[-30,15],[80,170]]) {
  const q=p.unproject(p.project(point));q.forEach((x,i)=>assert(Math.abs(x-point[i])<1e-10));
 }
});
test('Tile corners align with the same Mercator projection',()=>{
 for(const t of visibleTiles(p,viewport)) {
  const n=2**t.z, lon=t.x/n*360-180, lat=Math.atan(Math.sinh(Math.PI*(1-2*t.y/n)))*180/Math.PI;
  const [x,y]=p.project([lat,lon]);assert(Math.abs(x-t.left)<1e-7);assert(Math.abs(y-t.top)<1e-7);
 }
});
test('Tiles cover visible letterbox bounds and remain bounded while zooming/panning',()=>{
 for(const [zoom,pan] of [[1,[0,0]],[4,[-1000,-400]],[.75,[30,50]]]) {
  const ts=visibleTiles(p,viewport,zoom,pan);assert(ts.length>0&&ts.length<=40);
  for(const tile of ts) {
   const l=tile.left*zoom+pan[0],t=tile.top*zoom+pan[1],size=tile.size*zoom;
   assert(l<viewport.left+viewport.width+1e-5&&l+size>viewport.left-1e-5);
   assert(t<viewport.top+viewport.height+1e-5&&t+size>viewport.top-1e-5);
  }
  const l=Math.min(...ts.map(t=>t.left*zoom+pan[0])),r=Math.max(...ts.map(t=>(t.left+t.size)*zoom+pan[0]));
  assert(l<=viewport.left&&r>=viewport.left+viewport.width-1e-6);
 }
});
test('Unchanged view uses stable tile keys',()=>{
 assert.deepEqual(visibleTiles(p,viewport),visibleTiles(p,viewport));
});
test('Tiny synthetic extent stays finite without inheriting the regional minimum extent',()=>{
 const a=createProjection([[37.1,127.1],[37.10001,127.10001]],true);
 assert(a.factor>p.factor);assert(a.project([37.1,127.1]).every(Number.isFinite));
 const single=createProjection([[37.1,127.1]],true);assert(single.project([37.1,127.1]).every(Number.isFinite));
});
test('Extreme latitude remains finite for supported Web Mercator bounds',()=>{
 const q=createProjection([[90,127],[-90,127]]);assert(q.project([90,127]).every(Number.isFinite));
 assert(visibleTiles(q,visibleSvgBounds(390,520)).every(t=>t.y>=0&&t.y<2**t.z));
});
