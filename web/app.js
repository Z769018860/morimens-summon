const TYPE_NAMES = {1:'命轮活动',2:'角色活动',10:'类别 10',16:'类别 16',17:'类别 17'};
const DEFAULT_RULES = {base:3.02,combined:5.02,pity:30,up:50};
const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
let data = {records:[],coverage:[],catalog:{characters:[],wheels:[],banners:[]}};
let catalog = new Map(), selectedType='all', visibleCount=40, search='', updateTimer=null;
let rules = {...DEFAULT_RULES,...JSON.parse(localStorage.getItem('morimens-rules') || '{}')};
let profile = {uid:'',nickname:'',...JSON.parse(localStorage.getItem('morimens-profile') || '{}')};
let excluded = new Set(JSON.parse(localStorage.getItem('morimens-excluded') || '[]'));
let overrides = JSON.parse(localStorage.getItem('morimens-overrides') || '{}');
const IMPORT_MODE = new URLSearchParams(location.search).get('import') === '1';
function saveExcluded(){localStorage.setItem('morimens-excluded', JSON.stringify([...excluded]))}
function saveOverrides(){localStorage.setItem('morimens-overrides', JSON.stringify(overrides))}
function isIncluded(r){return !excluded.has(keyOf(r))}

function nameFor(type){return TYPE_NAMES[type] || `类别 ${type}`}
function dateOf(stamp){return new Date(stamp*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}
function bannerDate(value){return new Date(value.replaceAll('/','-').replace(' ','T')+':00+08:00')}
function niceDate(value){return value?.replaceAll('/','.') || '时间待补充'}
function bannerTitle(b){return b.titleZh||b.title}
function keyOf(r){return `${r.history_type}:${r.ordinal}`}
function metaOf(r){
  // Live data from /api/data has no rarity/kind of its own and relies on the
  // local catalog. An imported export already carries rarity/kind per record
  // (from whoever exported it) — trust those first since no catalog ships
  // inside the JSON, only fall back to the live catalog for portrait icons.
  const fromCatalog=catalog.get(r.name);
  return {rarity:r.rarity??fromCatalog?.rarity??'未知',icon:fromCatalog?.icon??null,kind:r.kind??fromCatalog?.kind??'unknown'};
}
function isSSR(r){return metaOf(r).rarity==='SSR'}
function categoryRows(type){return data.records.filter(r=>Number(r.history_type)===Number(type)).sort((a,b)=>b.ordinal-a.ordinal)}
function coverageOf(type){return data.coverage.find(c=>Number(c.history_type)===Number(type))}
function activeBanners(r){const t=r.timestamp*1000,kind=metaOf(r).kind;return data.catalog.banners.filter(b=>bannerDate(b.startDate).getTime()<=t && t<bannerDate(b.endDate).getTime() && (b.featuredZh||[]).some(name=>catalog.get(name)?.kind===kind))}
// An imported export has no banner calendar of its own (only the per-record
// result the exporter already computed), so the live banner calendar is only
// usable when it was actually loaded alongside the records (local tool, or a
// future export that embeds it). Everything below falls back to whatever the
// record already says once that calendar isn't available, instead of
// silently re-deciding everyone as "unknown".
function catalogReady(){return data.catalog.banners.length>0}
function candidateBanners(r){return catalogReady()&&[1,2].includes(Number(r.history_type))?activeBanners(r):[]}
function resolvedBanner(r){
  // The History RPC has no per-pull pool ID. Only a single matching event
  // in the confirmed history family permits a definite UP/off-rate verdict,
  // unless the player has manually resolved it on the records page.
  const override=overrides[keyOf(r)];
  if(override==='none')return null;
  if(override)return data.catalog.banners.find(b=>b.id===override)||null;
  if(!catalogReady())return null;
  const candidates=candidateBanners(r);
  return candidates.length===1?candidates[0]:null;
}
function bannerAssignmentOf(r){
  if(overrides[keyOf(r)])return 'manual';
  if(!catalogReady())return r.bannerAssignment||'unknown';
  return resolvedBanner(r)?'inferred':(candidateBanners(r).length?'ambiguous':'unknown');
}
function upState(r){
  if(!isSSR(r))return null;
  if(overrides[keyOf(r)]==='none')return 'unknown';
  if(!catalogReady())return r.upStatus??'unknown';
  const banner=resolvedBanner(r);
  if(banner)return (banner.featuredZh||[]).includes(r.name)?'up':'off';
  return 'unknown';
}
function upLabel(state){return ({up:'命中 UP',off:'歪',unknown:'无法确认'})[state]||''}
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
// Multi-pull batches (5/10 连) all land with the exact same server timestamp;
// grouping on (history_type, timestamp) is how other summon tools reconstruct
// draw sessions when the RPC itself doesn't expose a pull-count field.
function pullSessions(){
  const groups=new Map();
  for(const r of data.records){
    if(!isIncluded(r))continue;
    const key=`${r.history_type}:${r.timestamp}`;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(r);
  }
  return [...groups.values()];
}
function computeAchievements(){
  const sessions=pullSessions(),intervals=allIntervals(),pity=Number(rules.pity)||30;
  const tags=[];
  const doubleGold=sessions.filter(s=>s.filter(isSSR).length>=2).length;
  const tripleInFive=sessions.filter(s=>s.length>=5&&s.filter(isSSR).length>=3).length;
  let backToBack=0;
  for(const c of data.coverage){
    const rows=categoryRows(c.history_type).filter(isIncluded).sort((a,b)=>a.ordinal-b.ordinal);
    for(let i=1;i<rows.length;i++)if(isSSR(rows[i])&&isSSR(rows[i-1])&&rows[i].ordinal-rows[i-1].ordinal===1)backToBack++;
  }
  const valid=[...intervals.values()].filter(Number.isFinite);
  const pityHits=valid.filter(n=>n>=pity).length;
  const earliest=valid.length?Math.min(...valid):null;
  const latest=valid.length?Math.max(...valid):null;
  if(doubleGold>0)tags.push({key:'double-gold',label:'双黄',icon:'✨',detail:`${doubleGold} 次同一批抽中 2 个及以上 SSR`});
  if(tripleInFive>0)tags.push({key:'triple-five',label:'五连三金',icon:'🌟',detail:`${tripleInFive} 次 5 连里出现 3 个及以上 SSR`});
  if(backToBack>0)tags.push({key:'back-to-back',label:'连闪',icon:'⚡',detail:`${backToBack} 次背靠背连续两抽都是 SSR`});
  if(pityHits>=3)tags.push({key:'pity-king',label:'保底之王',icon:'👑',detail:`${pityHits} 次撑到 ${pity} 抽硬保底才出货`});
  if(earliest!==null&&earliest<=5)tags.push({key:'lucky-star',label:'欧皇时刻',icon:'🍀',detail:`最快 ${earliest} 抽就出 SSR`});
  if(latest!==null&&latest>=Math.round(pity*1.3))tags.push({key:'unlucky-alert',label:'非酋警报',icon:'🌧️',detail:`最惨一次抽了 ${latest} 抽才出货`});
  return tags;
}
function portrait(r){const src=asset(r);return src?`<img src="${html(src)}" alt="">`:`<div class="placeholder">✦</div>`}
function renderOverview(){
  const rows=data.records.filter(isIncluded),ssrs=rows.filter(isSSR),intervals=allIntervals(),luck=luckLabel(intervals),complete=data.coverage.filter(c=>c.complete).length;
  const achievements=computeAchievements();
  $('achievement-badges').innerHTML=achievements.length?achievements.map(a=>`<span class="badge" title="${html(a.detail)}"><span>${a.icon}</span>${html(a.label)}</span>`).join(''):'<span class="subtle">暂无触发任何成就标签</span>';
  const valid=[...intervals.values()].filter(Number.isFinite),average=valid.length?valid.reduce((a,b)=>a+b,0)/valid.length:null;
  const up=ssrs.filter(r=>upState(r)==='up').length,off=ssrs.filter(r=>upState(r)==='off').length;
  $('coverage-notice').innerHTML=`<b>采集范围</b>　已保存 ${rows.length} 条真实通讯记录。${complete}/${data.coverage.length} 个类别已完整；缺页类别的保底进度和首个 SSR 间隔可能无法确定。`;
  $('stats').innerHTML=[['已记录抽数',rows.length,'跨全部类别'],['SSR 出现',ssrs.length,`观测出率 ${rows.length?(ssrs.length/rows.length*100).toFixed(2):'—'}%`],['平均出货抽数',average?average.toFixed(1):'—',`${valid.length} 个有效间隔`],['确认 UP / 歪',`${up} / ${off}`,`${ssrs.length-up-off} 次无法确认`],['完整类别',`${complete}/${data.coverage.length}`,'其余类别仍有缺页'],['欧非观察',luck.label,luck.detail]].map((v,i)=>`<div class="stat"><div class="label">${v[0]}</div><strong class="${i===1?'gold':''}">${html(v[1])}</strong><small>${html(v[2])}</small></div>`).join('');
  const max=Math.max(1,...data.coverage.map(x=>x.reported_total));
  $('pool-bars').innerHTML=data.coverage.map(c=>`<div class="pool-row"><span>${html(nameFor(c.history_type))}</span><div class="bar"><div class="fill ${c.complete?'':'unknown'}" style="width:${Math.max(2,c.known/max*100)}%"></div></div><b>${c.known}/${c.reported_total}</b></div>`).join('');
  $('recent-ssr').innerHTML=ssrs.sort((a,b)=>b.timestamp-a.timestamp).slice(0,5).map(r=>`<div class="ssr-mini">${portrait(r)}<div class="info"><b>${html(r.name)}</b><small>${html(nameFor(r.history_type))} · ${dateOf(r.timestamp)}</small></div><span class="pill ${upState(r)==='up'?'up':upState(r)==='off'?'off':'muted'}">${html(upLabel(upState(r)))}</span></div>`).join('')||'<div class="empty">暂无已确认 SSR 记录</div>';
  $('category-analysis').innerHTML=data.coverage.map(c=>{const group=categoryRows(c.history_type).filter(isIncluded),hits=group.filter(isSSR),values=[...ssrIntervals(c.history_type).values()].filter(Number.isFinite),mean=values.length?(values.reduce((a,b)=>a+b,0)/values.length).toFixed(1):'—';return `<div class="category-row"><b>${html(nameFor(c.history_type))}</b><span>${group.length} 抽 · SSR ${hits.length} · 出率 ${group.length?(hits.length/group.length*100).toFixed(1):'—'}%</span><strong>均 ${mean} 抽</strong></div>`}).join('');
  const buckets=[['1–5 抽',1,5],['6–10 抽',6,10],['11–20 抽',11,20],['21–30 抽',21,30],['31 抽以上',31,Infinity]],counts=buckets.map(([,lo,hi])=>valid.filter(n=>n>=lo&&n<=hi).length),maxBucket=Math.max(1,...counts);
  $('interval-analysis').innerHTML=buckets.map(([label],i)=>`<div class="interval-row"><span>${label}</span><div class="bar"><div class="fill" style="width:${counts[i]/maxBucket*100}%"></div></div><b>${counts[i]}</b></div>`).join('')+`<p class="analysis-note">${valid.length} 个连续区间；缺页或首段未覆盖不纳入平均值。</p>`;
  const hero=data.catalog.characters.find(x=>x.name==='蚀灭·萝坦'&&x.icon)||data.catalog.characters.find(x=>x.icon);
  // An empty/missing src renders as a broken-image icon in most browsers —
  // hide the element instead (e.g. import mode has no catalog, so no art).
  $('hero-portrait').hidden=!hero?.icon;
  if(hero?.icon)$('hero-portrait').src=hero.icon;
}
function renderTabs(){const tabs=[['all','全部'],...data.coverage.map(c=>[String(c.history_type),nameFor(c.history_type)])];$('pool-tabs').innerHTML=tabs.map(([id,label])=>`<button data-type="${html(id)}" class="${selectedType===id?'active':''}">${html(label)}</button>`).join('')}
function renderHistory(){
  renderTabs();const intervals=allIntervals();let rows=data.records.filter(r=>selectedType==='all'||String(r.history_type)===selectedType);
  const counted=rows.length;
  rows=rows.filter(isSSR);if(search)rows=rows.filter(r=>r.name.toLowerCase().includes(search)||String(r.item_tid).includes(search));
  rows.sort((a,b)=>b.timestamp-a.timestamp||b.ordinal-a.ordinal);
  const selected=selectedType==='all'?null:coverageOf(selectedType),off=rows.filter(r=>upState(r)==='off').length;
  $('history-summary').innerHTML=`<span>已计入 <b>${counted}</b> 抽</span><span>SSR <b>${rows.length}</b> 次</span><span>确认歪 <b>${off}</b> 次</span>${selected?`<span>覆盖 <b>${selected.known}/${selected.reported_total}</b> · ${selected.complete?'已完整':'仍有缺页'}</span>`:''}<span>SR 不单独展示</span>`;
  const shown=rows.slice(0,visibleCount);
  const firstType=selectedType==='all'?null:Number(selectedType);
  let latestProgress='';
  if(firstType!==null){const all=categoryRows(firstType),last=all.find(isSSR),top=all[0];if(last&&top){const known=new Set(all.map(x=>x.ordinal));let valid=true;for(let n=last.ordinal+1;n<=top.ordinal;n++)if(!known.has(n))valid=false;if(valid)latestProgress=`<div class="pity-now"><div class="pity-icon">✦</div><div><b>距离上次 SSR 已抽 ${top.ordinal-last.ordinal} 抽</b><small>${html(nameFor(firstType))} · 当前进度仅基于已保存记录</small></div><span>至今</span></div>`}}
  $('records').innerHTML=latestProgress+shown.map(r=>{
    const n=intervals.get(keyOf(r)),state=upState(r),banner=resolvedBanner(r),width=n==null?28:Math.min(100,Math.max(12,n/Math.max(1,Number(rules.pity))*100));
    const key=keyOf(r),candidates=candidateBanners(r),override=overrides[key]||'';
    const options=['<option value="">跟随自动判定</option>',...candidates.map(b=>`<option value="${html(b.id)}" ${override===b.id?'selected':''}>${html(bannerTitle(b))}</option>`),`<option value="none" ${override==='none'?'selected':''}>标记为无命中卡池</option>`];
    const selectHtml=(candidates.length||override)?`<select class="banner-select" data-key="${html(key)}" title="自动判定不确定时可手动选择这次 SSR 命中的卡池">${options.join('')}</select>`:'';
    return `<article class="ssr-row ${isIncluded(r)?'':'excluded'}">${portrait(r)}<div class="ssr-row-body"><div class="ssr-row-top"><div><b>${html(r.name)}</b><small>${html(nameFor(r.history_type))} · ${dateOf(r.timestamp)}${banner?` · ${html(bannerTitle(banner))}`:''}</small></div><div class="row-controls"><label class="check" title="取消勾选可把这条记录排除出统计（例如误记录的非抽卡获得）"><input type="checkbox" class="include-toggle" data-key="${html(key)}" ${isIncluded(r)?'checked':''}> 计入统计</label>${selectHtml}<span class="pill ${state==='up'?'up':state==='off'?'off':'muted'}">${html(upLabel(state))}</span></div></div><div class="draw-track"><div class="draw-fill ${n==null?'unknown':n<=Number(rules.pity)*.5?'lucky':n>=Number(rules.pity)*.85?'late':'normal'}" style="width:${width}%"><strong>${n==null?'抽数待补全':`${n} 抽`}</strong></div></div></div></article>`;
  }).join('')||'<div class="empty">没有符合条件的 SSR 记录</div>';
  $('load-more').hidden=visibleCount>=rows.length;
}
function renderBanners(){const now=Date.now();$('banner-grid').innerHTML=[...data.catalog.banners].sort((a,b)=>bannerDate(b.startDate)-bannerDate(a.startDate)).map(b=>{const start=bannerDate(b.startDate).getTime(),end=bannerDate(b.endDate).getTime(),live=start<=now&&now<end,days=Math.round((end-start)/86400000*10)/10;return `<article class="banner-card ${live?'live':''}"><div class="banner-top"><span class="kicker">${html(b.type?.toUpperCase()||'BANNER')}</span><span class="pill ${live?'up':'muted'}">${live?'进行中':now<start?'即将开启':'已结束'}</span></div><h3>${html(bannerTitle(b))}</h3><p class="english-title">${html(b.title)}</p><p>UP：${html((b.featuredZh||[]).join(' / ')||'自选或未列明')}</p><div class="dates">${html(niceDate(b.startDate))} → ${html(niceDate(b.endDate))}</div><div class="duration">持续 ${days} 天 · 服务器时间 UTC+8</div></article>`}).join('')}
function renderRules(){for(const [id,key] of [['base-rate','base'],['combined-rate','combined'],['hard-pity','pity'],['up-rate','up']])$(id).value=rules[key];$('profile-uid').value=profile.uid||'';$('profile-nickname').value=profile.nickname||''}
function showView(name){for(const v of document.querySelectorAll('.view'))v.classList.toggle('active',v.id===name);for(const n of document.querySelectorAll('.nav'))n.classList.toggle('active',n.dataset.view===name);$('view-name').textContent=({overview:'抽卡总览',history:'SSR 时间线',banners:'限时卡池',rules:'概率与规则'})[name];if(name==='history')renderHistory();if(name==='banners')renderBanners();if(name==='rules')renderRules();window.scrollTo({top:0,behavior:'smooth'})}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function structured(){
  const intervals=allIntervals();
  return {schemaVersion:3,generatedAt:new Date().toISOString(),source:'Summon.QuerySummonHistory',timeZone:'Asia/Shanghai',
    player:{uid:profile.uid||null,nickname:profile.nickname||null},
    coverage:data.coverage,rules:{...rules,verified:false},
    achievementTags:computeAchievements().map(a=>a.label),
    customBanners:Object.entries(overrides).filter(([,v])=>v).map(([key,bannerId])=>({key,bannerId})),
    records:data.records.map(r=>({...r,rarity:metaOf(r).rarity,kind:metaOf(r).kind,
      pullsSinceSSR:isSSR(r)?intervals.get(keyOf(r))??null:null,
      bannerId:resolvedBanner(r)?.id||(catalogReady()?null:r.bannerId||null),bannerAssignment:bannerAssignmentOf(r),
      upStatus:upState(r),excluded:!isIncluded(r)}))};
}
function exportJSON(){download(new Blob([JSON.stringify(structured(),null,2)],{type:'application/json;charset=utf-8'}),`morimens-summon-history${profile.nickname?'-'+profile.nickname:''}.json`)}
function exportCSV(){const s=structured(),fields=['history_type','ordinal','timestamp','item_tid','name','rarity','kind','pullsSinceSSR','bannerId','bannerAssignment','upStatus','excluded'];const quote=v=>'"'+String(v??'').replaceAll('"','""')+'"';download(new Blob(['\ufeff'+[fields.join(','),...s.records.map(r=>fields.map(f=>quote(r[f])).join(','))].join('\r\n')],{type:'text/csv;charset=utf-8'}),'morimens-summon-history.csv')}
function roundRect(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath()}
async function shareImage(){
  const W=1200,H=1760,canvas=document.createElement('canvas');canvas.width=W;canvas.height=H;const c=canvas.getContext('2d');
  const gradient=c.createLinearGradient(0,0,W,H);gradient.addColorStop(0,'#1c2834');gradient.addColorStop(.55,'#121823');gradient.addColorStop(1,'#372f35');c.fillStyle=gradient;c.fillRect(0,0,W,H);
  c.fillStyle='#c7b98b14';c.font='440px sans-serif';c.fillText('✦',W-360,470);
  c.fillStyle='#dbc799';c.font='26px sans-serif';c.fillText('银钥档案  /  忘忘看报  ·  MORIMENS SUMMON',72,86);
  c.fillStyle='#fff7e5';c.font='bold 58px sans-serif';c.fillText(profile.nickname?`${profile.nickname} 的抽卡手记`:'我的抽卡手记',72,172);
  c.fillStyle='#9fabb7';c.font='21px sans-serif';
  const subtitle=[profile.uid?`UID ${profile.uid}`:null,'真实通讯记录 · 本地生成','生成于 '+new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})].filter(Boolean).join('   ·   ');
  c.fillText(subtitle,72,210);
  const rows=data.records.filter(isIncluded),ssrs=rows.filter(isSSR),intervals=allIntervals(),luck=luckLabel(intervals);
  const valid=[...intervals.values()].filter(Number.isFinite),average=valid.length?valid.reduce((a,b)=>a+b,0)/valid.length:null;
  const up=ssrs.filter(r=>upState(r)==='up').length,off=ssrs.filter(r=>upState(r)==='off').length;
  const stats=[['已记录抽数',rows.length],['SSR 出现',ssrs.length],['平均出货',average?average.toFixed(1)+' 抽':'—'],['欧非观察',luck.label]];
  let x=72;const cardW=(W-144-3*18)/4;
  for(const [label,value] of stats){c.fillStyle='#242e3b';roundRect(c,x,248,cardW,128,14);c.fill();c.fillStyle='#aeb9c6';c.font='19px sans-serif';c.fillText(label,x+20,284);c.fillStyle='#e7d09c';c.font='bold 36px sans-serif';c.fillText(String(value),x+20,340);x+=cardW+18}
  c.fillStyle='#bfd9c8';c.font='16px sans-serif';c.fillText(`确认 UP ${up} 次 · 确认歪 ${off} 次 · ${ssrs.length-up-off} 次无法确认`,72,408);
  const achievements=computeAchievements();let ay=445;
  if(achievements.length){c.fillStyle='#f3e8d5';c.font='bold 26px sans-serif';c.fillText('成就标签',72,ay);ay+=14;let bx=72;
    for(const a of achievements){c.font='bold 21px sans-serif';const w=c.measureText(`${a.icon} ${a.label}`).width+42;if(bx+w>W-72){bx=72;ay+=52}
      c.fillStyle='#3a2f22';roundRect(c,bx,ay+16,w,40,20);c.fill();c.fillStyle='#f0d9a0';c.fillText(`${a.icon} ${a.label}`,bx+21,ay+43);bx+=w+12}
    ay+=78;
  } else ay+=20;
  c.fillStyle='#f3e8d5';c.font='bold 28px sans-serif';c.fillText('类别覆盖',72,ay+18);let y=ay+70;
  for(const item of data.coverage){c.fillStyle='#adb8c3';c.font='22px sans-serif';c.fillText(nameFor(item.history_type),72,y);c.textAlign='right';c.fillStyle=item.complete?'#b6dbbd':'#dbbf8b';c.fillText(`${item.known} / ${item.reported_total}${item.complete?'  完整':'  缺页'}`,W-72,y);c.textAlign='left';y+=48}
  y+=36;c.fillStyle='#f3e8d5';c.font='bold 28px sans-serif';c.fillText('最近的 SSR',72,y);y+=40;
  for(const r of ssrs.sort((a,b)=>b.timestamp-a.timestamp).slice(0,6)){const icon=asset(r);if(icon){try{const img=new Image();img.src=icon;await img.decode();roundRect(c,72,y,70,70,10);c.save();c.clip();c.drawImage(img,72,y,70,70);c.restore()}catch{}}
    c.fillStyle='#eef1f4';c.font='bold 23px sans-serif';c.fillText(r.name,162,y+27);
    const n=intervals.get(keyOf(r));
    c.fillStyle='#a8b2be';c.font='18px sans-serif';c.fillText(`${nameFor(r.history_type)} · ${dateOf(r.timestamp)} · ${upLabel(upState(r))}${n!=null?` · ${n} 抽出货`:''}`,162,y+54);
    y+=86}
  c.fillStyle='#8c97a3';c.font='18px sans-serif';
  c.fillText('参考概率和欧非评价仅供娱乐；不完整类别不推算保底；标注为“排除”的记录不计入统计。',72,H-64);
  c.fillText('角色/命轮素材来自 SKeyDB，仅供本地个人分享 · 忘忘看报 · 非官方工具',72,H-34);
  canvas.toBlob(blob=>blob&&download(blob,`morimens-summon-share${profile.nickname?'-'+profile.nickname:''}.png`),'image/png');
}
function applyLoadedData(loaded){
  data={catalog:{characters:[],wheels:[],banners:[]},...loaded};
  catalog=new Map();
  for(const [kind,items] of [['character',data.catalog.characters],['wheel',data.catalog.wheels]])
    for(const item of items||[])catalog.set(item.name,{...item,kind});
  // Honor exclusion flags / manual banner assignments that came embedded in an imported file.
  for(const r of data.records){
    const key=keyOf(r);
    if(r.excluded&&!excluded.has(key)){excluded.add(key)}
    if(r.bannerAssignment==='manual'&&r.bannerId&&!overrides[key])overrides[key]=r.bannerId;
  }
  if(excluded.size)saveExcluded();
  if(Object.keys(overrides).length)saveOverrides();
  if(loaded.player&&(loaded.player.uid||loaded.player.nickname)&&!profile.uid&&!profile.nickname){
    profile={uid:loaded.player.uid||'',nickname:loaded.player.nickname||''};
    localStorage.setItem('morimens-profile',JSON.stringify(profile));
  }
}
async function refresh(){
  if(IMPORT_MODE){
    const raw=sessionStorage.getItem('morimens-import');
    if(!raw){$('coverage-notice').textContent='没有找到导入的 JSON，请回到主页重新选择文件。';return}
    try{
      applyLoadedData(JSON.parse(raw));
      $('sync-time').textContent=`已导入 ${data.records.length} 条记录 · 非本机实时数据`;
      $('coverage-notice').innerHTML='<b>导入模式</b>　当前展示的是你导入的 JSON 文件内容，不连接本机游戏，也不会把文件上传到任何服务器。“自动更新”仅在本地离线工具里可用。';
      $('refresh').disabled=true;$('refresh').title='导入模式下不可用，请使用本地离线工具更新数据';
      renderOverview();renderHistory();renderBanners();renderRules();
    }catch(error){$('coverage-notice').textContent=`导入的 JSON 解析失败：${error.message}`}
    return;
  }
  try{
    const response=await fetch('/api/data',{cache:'no-store'});
    if(!response.ok)throw new Error('数据读取失败');
    applyLoadedData(await response.json());
    $('sync-time').textContent=`${data.records.length} 条已保存 · ${new Date().toLocaleTimeString('zh-CN')}`;
    renderOverview();renderHistory();renderBanners();renderRules();
  }catch(error){$('coverage-notice').textContent=`读取失败：${error.message}`}
}
let updateWasRunning=false;
async function pollUpdate(){
  try{const response=await fetch('/api/update-status',{cache:'no-store'}),status=await response.json();
    const total=status.coverage.reduce((n,c)=>n+c.reported_total,0),known=status.coverage.reduce((n,c)=>n+c.known,0),percent=total?Math.round(known/total*100):0;
    $('progress-fill').style.width=`${percent}%`;$('progress-text').textContent=status.message;$('progress-number').textContent=`${known} / ${total} 条 · ${percent}%`;
    $('progress-coverage').textContent=status.coverage.map(c=>`${nameFor(c.history_type)} ${c.known}/${c.reported_total}`).join('　·　');
    $('progress-log').replaceChildren(...status.log.slice(-5).map(line=>{const item=document.createElement('div');item.textContent=line;return item}));
    $('start-update').disabled=status.running;$('start-update').textContent=status.running?'正在更新…':status.result?'再次尝试更新':'开始更新';
    $('wait-update').disabled=status.running;
    if(updateWasRunning&&!status.running)await refresh();updateWasRunning=status.running;
    if(status.running&&!updateTimer)updateTimer=setInterval(pollUpdate,1200);
    if(!status.running&&updateTimer){clearInterval(updateTimer);updateTimer=null}
  }catch(error){$('progress-text').textContent=`读取进度失败：${error.message}`}
}
async function openUpdate(){$('update-modal').hidden=false;await pollUpdate()}
async function startUpdate(mode='now'){
  $('start-update').disabled=true;$('progress-text').textContent='正在启动采集…';
  try{const response=await fetch('/api/update',{method:'POST',headers:{'X-Morimens-Action':'update','Content-Type':'application/json'},body:JSON.stringify({mode})});const result=await response.json();if(!response.ok)throw new Error(result.error||'更新启动失败');updateWasRunning=true;await pollUpdate()}
  catch(error){$('progress-text').textContent=error.message;$('start-update').disabled=false}
}
document.addEventListener('click',e=>{const nav=e.target.closest('[data-view]');if(nav)showView(nav.dataset.view);const target=e.target.closest('[data-target]');if(target)showView(target.dataset.target);const tab=e.target.closest('[data-type]');if(tab){selectedType=tab.dataset.type;visibleCount=40;renderHistory()}});
$('refresh').onclick=openUpdate;$('start-update').onclick=()=>startUpdate();$('wait-update').onclick=()=>startUpdate('wait');$('close-update').onclick=()=>{$('update-modal').hidden=true};$('cancel-update').onclick=()=>{$('update-modal').hidden=true};$('go-history').onclick=()=>showView('history');$('share').onclick=shareImage;$('export-json').onclick=exportJSON;$('export-csv').onclick=exportCSV;$('load-more').onclick=()=>{visibleCount+=50;renderHistory()};$('search').oninput=e=>{search=e.target.value.trim().toLowerCase();visibleCount=40;renderHistory()};
$('save-rules').onclick=()=>{const next={base:Number($('base-rate').value),combined:Number($('combined-rate').value),pity:Number($('hard-pity').value),up:Number($('up-rate').value)};if(!(next.base>0&&next.base<=100&&next.combined>0&&next.combined<=100&&Number.isInteger(next.pity)&&next.pity>0&&next.up>=0&&next.up<=100)){alert('请填写有效的概率与保底抽数');return}rules=next;localStorage.setItem('morimens-rules',JSON.stringify(rules));renderOverview();alert('参考规则已保存在本机浏览器')};
$('save-profile').onclick=()=>{profile={uid:$('profile-uid').value.trim(),nickname:$('profile-nickname').value.trim()};localStorage.setItem('morimens-profile',JSON.stringify(profile));alert('昵称/UID 已保存在本机浏览器，仅在你导出 JSON 或生成分享图时附带，不会上传。')};
document.addEventListener('change',e=>{
  const toggle=e.target.closest('.include-toggle');
  if(toggle){const key=toggle.dataset.key;if(toggle.checked)excluded.delete(key);else excluded.add(key);saveExcluded();renderOverview();renderHistory();return}
  const select=e.target.closest('.banner-select');
  if(select){const key=select.dataset.key,value=select.value;if(value)overrides[key]=value;else delete overrides[key];saveOverrides();renderOverview();renderHistory()}
});
if(IMPORT_MODE)document.body.classList.add('import-mode');
refresh();
