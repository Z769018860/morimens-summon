const TYPE_NAMES = {1:'命轮活动',2:'角色活动',10:'类别 10',16:'类别 16',17:'类别 17'};
const DEFAULT_RULES = {base:3.02,combined:5.02,pity:30,up:50};
const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
let data = {records:[],coverage:[],catalog:{characters:[],wheels:[],banners:[]}};
let catalog = new Map(), selectedType='all', visibleCount=40, search='', ssrOnly=false;
let annotations = JSON.parse(localStorage.getItem('morimens-banner-annotations') || '{}');
let rules = {...DEFAULT_RULES,...JSON.parse(localStorage.getItem('morimens-rules') || '{}')};

function nameFor(type){return TYPE_NAMES[type] || `类别 ${type}`}
function dateOf(stamp){return new Date(stamp*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}
function bannerDate(value){return new Date(value.replaceAll('/','-').replace(' ','T')+':00+08:00')}
function niceDate(value){return value?.replaceAll('/','.') || '时间待补充'}
function keyOf(r){return `${r.history_type}:${r.ordinal}`}
function metaOf(r){return catalog.get(r.name) || {rarity:'未知',icon:null,kind:'unknown'}}
function isSSR(r){return metaOf(r).rarity==='SSR'}
function categoryRows(type){return data.records.filter(r=>Number(r.history_type)===Number(type)).sort((a,b)=>b.ordinal-a.ordinal)}
function coverageOf(type){return data.coverage.find(c=>Number(c.history_type)===Number(type))}
function activeBanners(r){const t=r.timestamp*1000,kind=metaOf(r).kind;return data.catalog.banners.filter(b=>bannerDate(b.startDate).getTime()<=t && t<bannerDate(b.endDate).getTime() && (b.featuredZh||[]).some(name=>catalog.get(name)?.kind===kind))}
function selectedBanner(r){return data.catalog.banners.find(b=>b.id===annotations[keyOf(r)])}
function upState(r){
  if(!isSSR(r))return null;
  const chosen=selectedBanner(r);
  if(chosen)return (chosen.featuredZh||[]).includes(r.name)?'up':'off';
  if(activeBanners(r).some(b=>(b.featuredZh||[]).includes(r.name)))return 'possible';
  return 'unknown';
}
function upLabel(state){return ({up:'命中 UP',off:'歪',possible:'可能 UP',unknown:'UP 待确认'})[state]||''}
function ssrIntervals(type){
  const rows=categoryRows(type).sort((a,b)=>a.ordinal-b.ordinal),known=new Set(rows.map(r=>r.ordinal));
  let prev=null;const result=new Map();
  for(const row of rows){if(!isSSR(row))continue;
    const start=prev?prev.ordinal:0;
    const contiguous=Array.from({length:row.ordinal-start+(prev?0:1)},(_,i)=>start+i+(prev?1:0)).every(n=>known.has(n));
    result.set(keyOf(row),contiguous?row.ordinal-start+(prev?0:1):null);
    prev=row;
  }
  return result;
}
function allIntervals(){const out=new Map();for(const c of data.coverage)for(const [k,v] of ssrIntervals(c.history_type))out.set(k,v);return out}
function expectedPity(){const p=Number(rules.base)/100,n=Number(rules.pity);return p>0&&n>0?(1-Math.pow(1-p,n))/p:null}
function luckLabel(intervals){const values=[...intervals.values()].filter(v=>v!==null);if(values.length<5)return {label:'样本不足',detail:`${values.length} 次有效 SSR 间隔`};const avg=values.reduce((a,b)=>a+b,0)/values.length,expected=expectedPity();if(!expected)return {label:'规则待设置',detail:`平均 ${avg.toFixed(1)} 抽`};return {label:avg<=expected*.8?'偏欧':avg>=expected*1.2?'偏非':'正常波动',detail:`平均 ${avg.toFixed(1)} 抽 · 参考期望 ${expected.toFixed(1)} 抽`}}
function asset(r){return metaOf(r).icon||''}
function portrait(r){const src=asset(r);return src?`<img src="${html(src)}" alt="">`:`<div class="placeholder">✦</div>`}
function renderOverview(){
  const rows=data.records,ssrs=rows.filter(isSSR),intervals=allIntervals(),luck=luckLabel(intervals),complete=data.coverage.filter(c=>c.complete).length;
  $('coverage-notice').innerHTML=`<b>采集范围</b>　已保存 ${rows.length} 条真实通讯记录。${complete}/${data.coverage.length} 个类别已完整；缺页类别的保底进度和首个 SSR 间隔可能无法确定。`;
  $('stats').innerHTML=[['已记录抽数',rows.length,'跨全部类别'],['SSR 出现',ssrs.length,'已匹配 SKeyDB 稀有度'],['完整类别',`${complete}/${data.coverage.length}`,'其余类别仍有缺页'],['欧非观察',luck.label,luck.detail]].map((v,i)=>`<div class="stat"><div class="label">${v[0]}</div><strong class="${i===1?'gold':''}">${html(v[1])}</strong><small>${html(v[2])}</small></div>`).join('');
  const max=Math.max(1,...data.coverage.map(x=>x.reported_total));
  $('pool-bars').innerHTML=data.coverage.map(c=>`<div class="pool-row"><span>${html(nameFor(c.history_type))}</span><div class="bar"><div class="fill ${c.complete?'':'unknown'}" style="width:${Math.max(2,c.known/max*100)}%"></div></div><b>${c.known}/${c.reported_total}</b></div>`).join('');
  $('recent-ssr').innerHTML=ssrs.sort((a,b)=>b.timestamp-a.timestamp).slice(0,5).map(r=>`<div class="ssr-mini">${portrait(r)}<div class="info"><b>${html(r.name)}</b><small>${html(nameFor(r.history_type))} · ${dateOf(r.timestamp)}</small></div><span class="pill ${upState(r)==='up'?'up':upState(r)==='off'?'off':'muted'}">${html(upLabel(upState(r)))}</span></div>`).join('')||'<div class="empty">暂无已确认 SSR 记录</div>';
  const hero=data.catalog.characters.find(x=>x.name==='蚀灭·萝坦')||data.catalog.characters[0];$('hero-portrait').src=hero?.icon||'';
}
function renderTabs(){const tabs=[['all','全部'],...data.coverage.map(c=>[String(c.history_type),nameFor(c.history_type)])];$('pool-tabs').innerHTML=tabs.map(([id,label])=>`<button data-type="${html(id)}" class="${selectedType===id?'active':''}">${html(label)}</button>`).join('')}
function bannerSelector(r){if(!isSSR(r))return '';const candidates=activeBanners(r);if(!candidates.length)return '<span class="pill muted">无日程资料</span>';return `<select data-key="${html(keyOf(r))}" aria-label="指定抽卡所属限时卡池"><option value="">指定当期卡池…</option>${candidates.map(b=>`<option value="${html(b.id)}" ${annotations[keyOf(r)]===b.id?'selected':''}>${html(b.title)} · ${html((b.featuredZh||[]).join(' / '))}</option>`).join('')}</select>`}
function renderHistory(){
  renderTabs();const intervals=allIntervals();let rows=data.records.filter(r=>selectedType==='all'||String(r.history_type)===selectedType);
  if(ssrOnly)rows=rows.filter(isSSR);if(search)rows=rows.filter(r=>r.name.toLowerCase().includes(search)||String(r.item_tid).includes(search));
  rows.sort((a,b)=>b.timestamp-a.timestamp||b.ordinal-a.ordinal);
  const selected=selectedType==='all'?null:coverageOf(selectedType),ssrCount=rows.filter(isSSR).length;
  $('history-summary').innerHTML=`<span>显示 <b>${rows.length}</b> 条</span><span>SSR <b>${ssrCount}</b> 条</span>${selected?`<span>覆盖 <b>${selected.known}/${selected.reported_total}</b> · ${selected.complete?'已完整':'仍有缺页'}</span>`:''}`;
  $('records').innerHTML=rows.slice(0,visibleCount).map(r=>{const meta=metaOf(r),n=intervals.get(keyOf(r)),state=upState(r);return `<article class="record ${isSSR(r)?'ssr':''}">${portrait(r)}<div class="record-name"><b>${html(r.name)}</b><small>${html(nameFor(r.history_type))} · ${html(meta.rarity)} · TID ${r.item_tid}</small></div><div class="record-meta">${isSSR(r)?`本次 SSR <b>${n==null?'抽数待补全':`${n} 抽`}</b>`:`历史序号 #${r.ordinal+1}`}</div>${state?`<span class="pill ${state==='up'?'up':state==='off'?'off':'muted'}">${html(upLabel(state))}</span>`:''}${bannerSelector(r)}<span class="record-date">${dateOf(r.timestamp)}</span></article>`}).join('')||'<div class="empty">没有符合条件的记录</div>';
  $('load-more').hidden=visibleCount>=rows.length;
}
function renderBanners(){const now=Date.now();$('banner-grid').innerHTML=[...data.catalog.banners].sort((a,b)=>bannerDate(b.startDate)-bannerDate(a.startDate)).map(b=>{const start=bannerDate(b.startDate).getTime(),end=bannerDate(b.endDate).getTime(),live=start<=now&&now<end,days=Math.round((end-start)/86400000*10)/10;return `<article class="banner-card ${live?'live':''}"><div class="banner-top"><span class="kicker">${html(b.type?.toUpperCase()||'BANNER')}</span><span class="pill ${live?'up':'muted'}">${live?'进行中':now<start?'即将开启':'已结束'}</span></div><h3>${html(b.title)}</h3><p>UP：${html((b.featuredZh||[]).join(' / ')||'自选或未列明')}</p><div class="dates">${html(niceDate(b.startDate))} → ${html(niceDate(b.endDate))}</div><div class="duration">持续 ${days} 天 · 服务器时间 UTC+8</div></article>`}).join('')}
function renderRules(){for(const [id,key] of [['base-rate','base'],['combined-rate','combined'],['hard-pity','pity'],['up-rate','up']])$(id).value=rules[key]}
function showView(name){for(const v of document.querySelectorAll('.view'))v.classList.toggle('active',v.id===name);for(const n of document.querySelectorAll('.nav'))n.classList.toggle('active',n.dataset.view===name);$('view-name').textContent=({overview:'抽卡总览',history:'抽卡记录',banners:'限时卡池',rules:'概率与规则'})[name];if(name==='history')renderHistory();if(name==='banners')renderBanners();if(name==='rules')renderRules();window.scrollTo({top:0,behavior:'smooth'})}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function structured(){const intervals=allIntervals();return {schemaVersion:1,generatedAt:new Date().toISOString(),source:'Summon.QuerySummonHistory',timeZone:'Asia/Shanghai',coverage:data.coverage,rules:{...rules,verified:false},records:data.records.map(r=>({...r,rarity:metaOf(r).rarity,kind:metaOf(r).kind,pullsSinceSSR:intervals.get(keyOf(r))??null,bannerId:annotations[keyOf(r)]||null,upStatus:upState(r)}))}}
function exportJSON(){download(new Blob([JSON.stringify(structured(),null,2)],{type:'application/json;charset=utf-8'}),'morimens-summon-history.json')}
function exportCSV(){const s=structured(),fields=['history_type','ordinal','timestamp','item_tid','name','rarity','kind','pullsSinceSSR','bannerId','upStatus'];const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';download(new Blob(['\ufeff'+[fields.join(','),...s.records.map(r=>fields.map(f=>quote(r[f])).join(','))].join('\r\n')],{type:'text/csv;charset=utf-8'}),'morimens-summon-history.csv')}
async function shareImage(){
  const canvas=document.createElement('canvas');canvas.width=1200;canvas.height=1550;const c=canvas.getContext('2d');
  const gradient=c.createLinearGradient(0,0,1200,1550);gradient.addColorStop(0,'#19242f');gradient.addColorStop(.65,'#111827');gradient.addColorStop(1,'#342d32');c.fillStyle=gradient;c.fillRect(0,0,1200,1550);
  c.fillStyle='#dbc799';c.font='28px sans-serif';c.fillText('✦  银钥档案  /  MORIMENS',72,89);c.fillStyle='#fff7e5';c.font='bold 62px sans-serif';c.fillText('我的抽卡手记',72,185);
  c.fillStyle='#9fabb7';c.font='23px sans-serif';c.fillText('真实通讯记录 · 本地生成 · '+new Date().toLocaleDateString('zh-CN'),72,229);
  const ssrs=data.records.filter(isSSR),luck=luckLabel(allIntervals());let x=72;
  for(const [label,value] of [['已记录抽数',data.records.length],['SSR',ssrs.length],['欧非观察',luck.label]]){c.fillStyle='#242e3b';c.fillRect(x,282,330,145);c.fillStyle='#aeb9c6';c.font='22px sans-serif';c.fillText(label,x+24,326);c.fillStyle='#e7d09c';c.font='bold 43px sans-serif';c.fillText(String(value),x+24,391);x+=356}
  c.fillStyle='#f3e8d5';c.font='bold 30px sans-serif';c.fillText('类别覆盖',72,504);let y=555;
  for(const item of data.coverage){c.fillStyle='#adb8c3';c.font='23px sans-serif';c.fillText(nameFor(item.history_type),72,y);c.textAlign='right';c.fillStyle=item.complete?'#b6dbbd':'#dbbf8b';c.fillText(`${item.known} / ${item.reported_total}${item.complete?'  完整':'  缺页'}`,1125,y);c.textAlign='left';y+=53}
  y+=40;c.fillStyle='#f3e8d5';c.font='bold 30px sans-serif';c.fillText('最近的 SSR',72,y);y+=42;
  for(const r of ssrs.sort((a,b)=>b.timestamp-a.timestamp).slice(0,5)){const icon=asset(r);if(icon){try{const img=new Image();img.src=icon;await img.decode();c.drawImage(img,72,y,74,74)}catch{}}c.fillStyle='#eef1f4';c.font='bold 24px sans-serif';c.fillText(r.name,165,y+29);c.fillStyle='#a8b2be';c.font='19px sans-serif';c.fillText(`${nameFor(r.history_type)} · ${dateOf(r.timestamp)} · ${upLabel(upState(r))}`,165,y+59);y+=105}
  c.fillStyle='#8c97a3';c.font='19px sans-serif';c.fillText('参考概率和欧非评价仅供娱乐；不完整类别不推算保底。',72,1460);c.fillText('SKeyDB 素材仅供本地个人分享 · 非官方工具',72,1494);
  canvas.toBlob(blob=>blob&&download(blob,'morimens-summon-share.png'),'image/png');
}
async function refresh(){try{const response=await fetch('/api/data',{cache:'no-store'});if(!response.ok)throw new Error('数据读取失败');data=await response.json();catalog=new Map();for(const [kind,items] of [['character',data.catalog.characters],['wheel',data.catalog.wheels]])for(const item of items||[])catalog.set(item.name,{...item,kind});$('sync-time').textContent=`${data.records.length} 条已保存 · ${new Date().toLocaleTimeString('zh-CN')}`;renderOverview();renderHistory();renderBanners();renderRules()}catch(error){$('coverage-notice').textContent=`读取失败：${error.message}`}}
document.addEventListener('click',e=>{const nav=e.target.closest('[data-view]');if(nav)showView(nav.dataset.view);const target=e.target.closest('[data-target]');if(target)showView(target.dataset.target);const tab=e.target.closest('[data-type]');if(tab){selectedType=tab.dataset.type;visibleCount=40;renderHistory()}});
document.addEventListener('change',e=>{if(e.target.matches('select[data-key]')){if(e.target.value)annotations[e.target.dataset.key]=e.target.value;else delete annotations[e.target.dataset.key];localStorage.setItem('morimens-banner-annotations',JSON.stringify(annotations));renderHistory();renderOverview()}});
$('refresh').onclick=refresh;$('go-history').onclick=()=>showView('history');$('share').onclick=shareImage;$('export-json').onclick=exportJSON;$('export-csv').onclick=exportCSV;$('load-more').onclick=()=>{visibleCount+=50;renderHistory()};$('search').oninput=e=>{search=e.target.value.trim().toLowerCase();visibleCount=40;renderHistory()};$('ssr-only').onchange=e=>{ssrOnly=e.target.checked;visibleCount=40;renderHistory()};
$('save-rules').onclick=()=>{const next={base:Number($('base-rate').value),combined:Number($('combined-rate').value),pity:Number($('hard-pity').value),up:Number($('up-rate').value)};if(!(next.base>0&&next.base<=100&&next.combined>0&&next.combined<=100&&Number.isInteger(next.pity)&&next.pity>0&&next.up>=0&&next.up<=100)){alert('请填写有效的概率与保底抽数');return}rules=next;localStorage.setItem('morimens-rules',JSON.stringify(rules));renderOverview();alert('参考规则已保存在本机浏览器')};
refresh();
