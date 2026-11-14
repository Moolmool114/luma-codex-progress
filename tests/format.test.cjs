const {test}=require('node:test');
const assert=require('node:assert/strict');
const f=require('../ui/format.js');
test('ETA uses whole minutes, then hours and minutes',()=>{
  assert.equal(f.remaining(23),'1 分钟');
  assert.equal(f.remaining(300),'5 分钟');
  assert.equal(f.remaining(3600),'1 小时');
  assert.equal(f.remaining(3900),'1 小时 5 分');
  assert.equal(f.remaining(null),'待估算');
});
test('running clock retains seconds without changing ETA units',()=>{
  assert.equal(f.elapsed(65),'01:05');assert.equal(f.elapsed(3665),'01:01:05');
});
test('finish time identifies crossing midnight',()=>{
  const now=new Date(2026,10,14,23,50).getTime()/1000;
  assert.equal(f.clock(now+1800,now),'明天 00:20');
});
test('ranges avoid decimal hours',()=>{
  assert.equal(f.range([60,180]),'1–3 分钟');
  assert.equal(f.range([3600,4500]),'1 小时 – 1 小时 15 分');
});
