const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const fields = {'date-from': {value: '2026-06-13'}, 'date-to': {value: '2026-06-13'}};
const storage = new Map();
const context = vm.createContext({
  localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
  document: {getElementById: id => fields[id]},
});
const source = fs.readFileSync('web/app.js', 'utf8').split("document.addEventListener('click'")[0];
vm.runInContext(source, context);
vm.runInContext(`
  selectedType='2';
  catalog=new Map([['甲',{rarity:'SSR',kind:'character'}],['乙',{rarity:'SSR',kind:'character'}]]);
  data={records:[
    {history_type:2,ordinal:0,name:'甲',timestamp:Date.parse('2026-06-12T20:00:00+08:00')/1000},
    {history_type:2,ordinal:1,name:'乙',timestamp:Date.parse('2026-06-12T20:01:00+08:00')/1000},
    {history_type:2,ordinal:2,name:'甲',timestamp:Date.parse('2026-06-13T10:00:00+08:00')/1000},
    {history_type:2,ordinal:3,name:'乙',timestamp:Date.parse('2026-06-13T10:01:00+08:00')/1000},
    {history_type:2,ordinal:4,name:'甲',timestamp:Date.parse('2026-06-13T10:02:00+08:00')/1000},
    {history_type:1,ordinal:0,name:'甲',timestamp:Date.parse('2026-06-13T10:00:00+08:00')/1000},
  ],coverage:[{history_type:2},{history_type:1}],catalog:{banners:[]}};
  customBanners=[{id:'custom-1',titleZh:'测试池',featuredZh:['甲']}];
  assignments={'2:2':'custom-1','2:3':'custom-1','2:4':'custom-1'};
`, context);
assert.equal(vm.runInContext('filteredRows().length', context), 3);
assert.equal(vm.runInContext("upState(data.records[3])", context), 'off');
assert.equal(vm.runInContext("upState(data.records[2])", context), 'up');
assert.equal(vm.runInContext("achievementTags().includes('五连三金')", context), true);
assert.equal(vm.runInContext("achievementTags().includes('双黄')", context), true);
vm.runInContext("customBanners[0].featuredZh=[]", context);
assert.equal(vm.runInContext("upState(data.records[3])", context), 'unknown');
console.log('Web logic checks passed');
