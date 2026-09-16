const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const c={};vm.runInNewContext(fs.readFileSync('static/js/spin_workflow.js','utf8').replaceAll('export class','class').replaceAll('export function','function')+';this.SpinWorkflow=SpinWorkflow;',c);
function setup(frames){const actions=[],states=[];let i=0;const flow=new c.SpinWorkflow({capture:async()=>frames[Math.min(i++,frames.length-1)],input:async cmd=>actions.push(cmd.action),analyze:f=>f,candidate:()=>({x:.3,y:.6}),report:(s,m)=>states.push([s,m]),wait:async()=>{}});return {flow,actions,states};}
const map={kind:'map'},detail={kind:'detail',color:'cyan',discY:.5},purple={kind:'detail',color:'purple',discY:.5};
test('one full cycle verifies result and returns to map',async()=>{const f=setup([map,detail,detail,purple,purple,map]);await f.flow.run();assert.deepEqual(f.actions,['tap','swipe','back']);assert.equal(f.states.at(-1)[0],'done');});
test('no swipe when detail screen never appears',async()=>{const f=setup([map]);await f.flow.run();assert.deepEqual(f.actions,['tap']);assert.equal(f.states.at(-1)[0],'error');});
test('unknown initial screen sends no commands',async()=>{const f=setup([{kind:'unknown'}]);await f.flow.run();assert.deepEqual(f.actions,[]);});
test('stop after tap prevents all later input',async()=>{const f=setup([map,detail]);f.flow.input=async cmd=>{f.actions.push(cmd.action);f.flow.stop();};await f.flow.run();assert.deepEqual(f.actions,['tap']);assert.equal(f.states.at(-1)[0],'stopped');});
test('purple detail skips swipe',async()=>{const f=setup([map,purple,purple,map]);await f.flow.run();assert.deepEqual(f.actions,['tap','back']);});
test('unconfirmed result never retries swipe or reports success',async()=>{const f=setup([map,detail]);await f.flow.run();assert.equal(f.actions.filter(a=>a==='swipe').length,1);assert.ok(f.states.some(([s,m])=>m.includes('ไม่ยืนยันผล')));});
