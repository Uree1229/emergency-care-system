const {chromium}=require('C:/Users/PC/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try {
  const page=await browser.newPage({viewport:{width:1280,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.goto(pathToFileURL(path.join(__dirname,'index.html')).href);
  await page.selectOption('#count','5');await page.click('#start');
  await page.click('[data-id="SIM-1"][data-action="accept"]');
  await page.click('[data-id="SIM-2"][data-action="open"]');
  await page.selectOption('#reason','인력');await page.click('#send');
  assert.match(await page.locator('#metrics').innerText(),/완료 2\/5/);
  assert.match(await page.locator('#metrics').innerText(),/오응답 0/);
  await page.screenshot({path:path.join(__dirname,'preview.png'),fullPage:true});
  await page.click('#finish');await page.selectOption('#mode','baseline');await page.click('#start');
  await page.click('[data-id="SIM-1"][data-action="open"]');
  await page.selectOption('#statusChoice','accept');await page.click('#send');
  assert.match(await page.locator('#metrics').innerText(),/완료 1\/5/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.screenshot({path:path.join(__dirname,'preview-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: fast response, decline reason, comparison mode, mobile width, no JS errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
