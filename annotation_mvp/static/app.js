'use strict';
const $ = id => document.getElementById(id);
let token = '', session = null, busy = false, imageReady = false, started = 0, currentView = 'setup', toastTimer;
let explaining = false;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6500); }
async function api(path, data) {
  const r = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(data)});
  const result = await r.json(); if (!r.ok) throw new Error(result.error || '请求失败'); return result;
}
async function refresh() {
  const s = await api('/api/status'); token = s.token;
  if (!$('reason-options').children.length) for (const [value,text] of Object.entries(s.reason_options||{})) {
    const label=document.createElement('label'), input=document.createElement('input'); input.type='checkbox';input.value=value;input.name='negative_reason';
    label.append(input,document.createTextNode(text));$('reason-options').append(label);
    input.onchange=()=>{ if(input.checked) for(const other of $('reason-options').querySelectorAll('input')) if(other!==input&&(value==='unsure'||other.value==='unsure'))other.checked=false; };
  }
  $('image-count').textContent = s.count; $('split-count').textContent = s.count ? `${s.splits.train||0} / ${s.splits.validation||0} / ${s.splits.test||0}` : '—';
  $('rater-count').textContent = s.annotators.length; $('phase').textContent = s.frozen ? '题库已锁定 · 可标注' : '准备中 · 可导入';
  $('import-btn').disabled = s.frozen; $('freeze-btn').disabled = s.frozen || !s.count; $('freeze-btn').textContent = s.frozen ? '题库已锁定' : '锁定题库';
  $('data-dir').textContent = s.data_dir; $('people').replaceChildren();
  if (!s.annotators.length) $('people').textContent = '尚未开始标注。';
  for (const a of s.annotators) { const row=document.createElement('div'), name=document.createElement('span'), count=document.createElement('span'); row.className='person'; name.textContent=a.name; count.textContent=`${a.done} / ${a.total} 已提交`; row.append(name,count); $('people').append(row); }
  return s;
}
function view(name) {
  currentView=name;
  document.querySelectorAll('.view').forEach(v => v.hidden = v.id !== name);
  document.querySelectorAll('.nav').forEach(v => v.classList.toggle('active',v.dataset.view===name));
  $('breadcrumb').textContent={setup:'图片准备',label:'开始标注',export:'数据与导出'}[name];
  refresh().catch(e=>toast(e.message));
}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>view(b.dataset.view)));
$('import-form').addEventListener('submit',async e=>{
  e.preventDefault(); if(busy)return; busy=true; $('import-btn').disabled=true; $('import-result').textContent='正在复制图片并校验，请稍候…';
  try { const s=await api('/api/import',{path:$('folder').value.trim(),source:$('source').value.trim(),grouping:$('grouping').value}); $('import-result').textContent=`已导入 ${s.imported} 张，跳过 ${s.duplicates} 张完全重复图片。`; await refresh(); }
  catch(e){$('import-result').textContent='导入未完成';toast(e.message);} finally{busy=false;await refresh().catch(()=>{});}
});
$('freeze-btn').addEventListener('click',async()=>{if(busy)return; if(!confirm('确认已检查母图分组和清单？锁定后，本轮不能再导入图片或改变划分。'))return;try{await api('/api/freeze',{});await refresh();view('label');toast('题库已锁定，可以开始标注。');}catch(e){toast(e.message);}});
function controls(){ $('comment').disabled=busy; $('undecidable-save').disabled=busy; $('undecidable-cancel').disabled=busy; for(const id of ['yes','no','undecidable']) $(id).disabled=busy||!imageReady||!session?.task; $('skip').disabled=busy||!session?.task; $('undo').disabled=busy||!session?.done; $('done-undo').disabled=busy||!session?.done; $('reason-save').disabled=busy||!session?.task; $('reason-cancel').disabled=busy; }
function openReasons(){
  explaining=true;$('reason-form').hidden=false;$('answer-controls').hidden=true;
  $('reason-title').textContent=session?.task?.reason_only?'为此前的“不通透”判断补充原因':'为什么觉得这张图不通透？';
  $('reason-cancel').hidden=!!session?.task?.reason_only;
  $('reason-error').textContent='';$('save-status').textContent='等待填写原因后保存';
  $('reason-form').scrollIntoView({block:'nearest',behavior:'smooth'});
}
function render(s){
  const wasExplaining=explaining;
  explaining=false;$('undecidable-form').hidden=true;$('undecidable-reason').value='';$('reason-form').hidden=true;$('negative-note').value='';$('reason-error').textContent='';
  $('reason-options').querySelectorAll('input').forEach(input=>input.checked=false);
  session=s; imageReady=false;
  $('comment-panel').hidden=!s.task;
  $('comment').value=s.task?.comment||'';
  if(s.task){try{const draft=localStorage.getItem('tongtou-comment:'+s.task.id);if(draft!==null)$('comment').value=draft;}catch(e){}}
  $('comment-status').textContent='提交答案时一起保存；未提交内容仅暂存在当前浏览器。'; $('identity-panel').hidden=true;$('workspace').hidden=false;$('active-name').textContent=s.name;
  $('progress-text').textContent=`${s.done} / ${s.total} 已提交`;$('progress').max=s.total||1;$('progress').value=s.done;
  if(s.task?.reason_only)$('progress-text').textContent+=` · ${s.missing_reasons} 条待补原因`;
  $('uncertain').checked=false;$('completed').hidden=!!s.task;$('answer-controls').hidden=!s.task;$('photo').hidden=!s.task;
  $('image-message').hidden=!s.task;$('image-message').textContent='正在加载图片…';$('save-status').textContent=s.done?'已自动保存':'等待判断';
  if(s.task){$('photo').src=s.task.image_url;}else{$('photo').removeAttribute('src');$('image-message').hidden=false;$('image-message').textContent='本轮判断已全部记录';}
  controls();
  if(s.task?.reason_only)openReasons();
  else if(wasExplaining&&s.task)document.querySelector('.image-stage').scrollIntoView({block:'start'});
}
$('photo').addEventListener('load',()=>{imageReady=true;started=performance.now();$('image-message').hidden=true;controls();});
$('photo').addEventListener('error',()=>{imageReady=false;started=performance.now();$('image-message').hidden=false;$('image-message').textContent='图片无法显示，请刷新重试；仍无法显示时可跳过。';controls();});
$('photo').addEventListener('dblclick',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else if(imageReady)await $('photo').requestFullscreen();}catch(e){toast('当前浏览器不支持全屏，可使用浏览器缩放。');}});
$('start-form').addEventListener('submit',async e=>{e.preventDefault();if(busy)return;busy=true;try{const s=await api('/api/start',{name:$('name').value.trim()});localStorage.setItem('tongtou-name',s.name);render(s);}catch(e){toast(e.message);}finally{busy=false;controls();}});
async function answer(label,skip=false,withReasons=false,undecidable=false){
  if(busy||!session?.task||(!imageReady&&!skip&&!session.task.reason_only))return;
  if(label===0&&!skip&&!withReasons){openReasons();return;}
  const reasons=withReasons?Array.from($('reason-options').querySelectorAll('input:checked'),i=>i.value):[];
  const note=withReasons?$('negative-note').value.trim():'';
  if(withReasons&&(!reasons.length&&!note||reasons.includes('other')&&!note)){$('reason-error').textContent=reasons.includes('other')?'选择“其他”时，请填写具体说明。':'请选择原因或填写说明；也可选择“说不清具体原因”。';return;}
  busy=true;controls();$('save-status').textContent='正在保存…';
  const submittedTask=session.task.id;
  try{const result=await api('/api/answer',{name:session.name,task_id:session.task.id,label,uncertain:$('uncertain').checked,skip,undecidable,undecidable_reason:undecidable?$('undecidable-reason').value:'',negative_reasons:reasons,negative_note:note,comment:$('comment').value,duration_ms:Math.min(86400000,Math.max(0,Math.round(performance.now()-started)))});try{localStorage.removeItem('tongtou-comment:'+submittedTask);}catch(e){}render(result);}
  catch(e){toast(e.message);$('save-status').textContent='保存未确认，请重试';}finally{busy=false;controls();}
}
async function undo(){if(busy||!session?.done)return;busy=true;controls();try{render(await api('/api/undo',{name:session.name}));toast('已撤销上一条，请重新判断。');}catch(e){toast(e.message);}finally{busy=false;controls();}}
$('yes').onclick=()=>answer(1);$('no').onclick=()=>answer(0);$('skip').onclick=()=>answer(null,true);$('undo').onclick=undo;$('done-undo').onclick=undo;
$('undecidable').onclick=()=>{if(busy||!imageReady||!session?.task)return;explaining=true;$('undecidable-form').hidden=false;$('answer-controls').hidden=true;$('save-status').textContent='等待保存无法判断';$('undecidable-form').scrollIntoView({block:'nearest'});};
$('undecidable-form').onsubmit=e=>{e.preventDefault();answer(null,false,false,true);};
$('undecidable-cancel').onclick=()=>{if(busy)return;explaining=false;$('undecidable-form').hidden=true;$('answer-controls').hidden=false;$('save-status').textContent='等待判断';};
$('reason-form').onsubmit=e=>{e.preventDefault();answer(0,false,true);};
$('reason-cancel').onclick=()=>{if(busy||session?.task?.reason_only)return;explaining=false;$('reason-form').hidden=true;$('answer-controls').hidden=false;$('save-status').textContent='等待判断';};
$('leave').onclick=()=>{if(busy)return;session=null;$('identity-panel').hidden=false;$('workspace').hidden=true;refresh().catch(e=>toast(e.message));};
document.addEventListener('keydown',e=>{
  const active=document.activeElement;
  const editing=['SELECT','TEXTAREA'].includes(active.tagName)||(active.tagName==='INPUT'&&active.type!=='checkbox');
  if(e.repeat||currentView!=='label'||!session||busy||editing||e.metaKey||e.ctrlKey||e.altKey)return;
  if(explaining){if(e.key==='Escape'&&!session.task.reason_only)($('undecidable-form').hidden?$('reason-cancel'):$('undecidable-cancel')).click();return;}
  if(e.key==='1'){e.preventDefault();answer(1);}else if(e.key==='2'){e.preventDefault();answer(0);}else if(e.key==='3'){e.preventDefault();$('undecidable').click();}else if(e.key.toLowerCase()==='u'){$('uncertain').checked=!$('uncertain').checked;}else if(e.key.toLowerCase()==='s'){e.preventDefault();answer(null,true);}else if(e.key==='Backspace'){e.preventDefault();undo();}
});
$('name').value=localStorage.getItem('tongtou-name')||'';
refresh().catch(e=>toast('无法连接本地服务：'+e.message));

$('comment').addEventListener('input',()=>{if(!session?.task)return;try{localStorage.setItem('tongtou-comment:'+session.task.id,$('comment').value);$('comment-status').textContent='备注草稿已暂存在此浏览器；提交答案后写入数据库。';}catch(e){$('comment-status').textContent='浏览器暂存不可用，请提交答案以保存备注。';}});
