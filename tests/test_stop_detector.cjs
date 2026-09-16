const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const context={};vm.runInNewContext(fs.readFileSync('static/js/stop_detector.js','utf8').replace('export function','function')+';this.detectStops=detectStops;',context);
function frame(shape, purple=false){
  const width=450,height=800,data=new Uint8ClampedArray(width*height*4);
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const dx=x-130,dy=y-450;
    const r=(dx/24)**2+(dy/12)**2;
    if(shape==='ring' && r<.48){const i=(y*width+x)*4;data[i]=20;data[i+1]=70;data[i+2]=200;data[i+3]=255;}
    if(shape==='ring' ? r<=1 && r>=.48 : Math.abs(dx)+Math.abs(dy)<22){
      const i=(y*width+x)*4;data[i]=purple?190:30;data[i+1]=purple?80:210;data[i+2]=250;data[i+3]=255;
    }
  }
  return {width,height,data};
}
test('elliptical cyan ring qualifies with a high shape score',()=>{
 const found=context.detectStops(frame('ring'));
 assert.equal(found.length,1);assert.equal(found[0].kind,'ring');assert.ok(found[0].score>=85);assert.ok(found[0].eligible);
});
test('solid cyan diamond never qualifies even with maximum sensitivity',()=>{
 const found=context.detectStops(frame('diamond'),100);
 assert.equal(found.length,1);assert.equal(found[0].kind,'solid');assert.ok(found[0].score<40);assert.equal(found[0].eligible,false);
});
test('purple ring is classified but never eligible for a tap',()=>{
 const found=context.detectStops(frame('ring',true));assert.equal(found[0].kind,'ring');assert.equal(found[0].color,'purple');assert.equal(found[0].eligible,false);
});
