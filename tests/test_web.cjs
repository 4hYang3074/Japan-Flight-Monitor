const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('docs/index.html','utf8');
const source = html.slice(html.indexOf('function renderWatch('), html.indexOf('function renderPromos('));
test('zero-combination scans remain visible and do not affect averages',()=>{
  const target = {};
  const ctx={ $:()=>target, D:{core_nights:[6,7,8]},myt:x=>x,esc:x=>x,rm:x=>x,fmtD:x=>x };
  vm.createContext(ctx);vm.runInContext(source,ctx);
  const bad={at:'2026-10-09',slot:'周五 22:00',routes:[{combos:0,compared:0,cheaper:0,pricier:0,avg_diff_pct:null,quality:{D7:{status:'unpriced',message:'有航班但无报价'}}}]};
  ctx.renderWatch([bad]);assert.match(target.innerHTML,/有航班但无报价/);assert.match(target.innerHTML,/尚无可比较/);
  const good={at:'2026-10-07',slot:'周五 22:00',routes:[{combos:1,compared:1,cheaper:1,pricier:0,avg_diff_pct:-10}]};
  ctx.renderWatch([good,bad]);assert.match(target.innerHTML,/1 次，平均比例行扫描 -10%/);assert.match(target.innerHTML,/2026-10-09/);
  delete bad.routes[0].quality;ctx.renderWatch([bad]);assert.match(target.innerHTML,/旧记录未保存原因/);
});
