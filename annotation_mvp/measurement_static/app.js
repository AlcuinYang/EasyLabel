'use strict';
const $=id=>document.getElementById(id);
let token='',state=null,ready=false,busy=false,started=0,sessionStarted=0,restDismissed=false,pendingBin=null,pendingRt=null,epoch=0;
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(data)});const x=await r.json();if(!r.ok)throw Error(x.error||'请求失败');return x;}
function draftKey(kind,task=state){return 'measurement-'+kind+':'+(task?.experiment_id?task.experiment_id+':':'')+task?.task_id;}
function message(s){$('message').textContent=s;}
function disabled(value){for(const b of $('buttons').querySelectorAll('button'))b.disabled=value;}
function sizing(){if(!$('left').naturalWidth)return;const box=$('stage');const images=[$('left'),...($('right').hidden?[]:[$('right')])];if(images.some(i=>!i.naturalWidth))return;const ratios=images.map(i=>i.naturalWidth/i.naturalHeight);const height=Math.max(1,Math.min(box.clientHeight-40,(box.clientWidth-40-20*(images.length-1))/ratios.reduce((a,b)=>a+b,0),...images.map(i=>i.naturalHeight)));images.forEach((im,i)=>{im.style.height=height+'px';im.style.width=height*ratios[i]+'px';});}
async function loadImage(img,url){img.removeAttribute('src');img.src=url;await img.decode();}
function renderReport(source){const root=$('result-report');root.replaceChildren();const lines=source.split(/\r?\n/);let i=0;while(i<lines.length){const line=lines[i].trim();if(!line){i++;continue;}if(line.startsWith('```')){const block=document.createElement('pre');let body=[];i++;while(i<lines.length&&!lines[i].trim().startsWith('```'))body.push(lines[i++]);block.textContent=body.join('\n');root.append(block);i++;continue;}if(/^#{1,3} /.test(line)){const level=Math.min(4,(line.match(/^#+/)||['#'])[0].length+1);const heading=document.createElement('h'+level);heading.textContent=line.replace(/^#+\s*/, '');root.append(heading);i++;continue;}if(line.startsWith('|')&&i+1<lines.length&&/^\|[\s:|-]+\|$/.test(lines[i+1].trim())){const table=document.createElement('table');const head=document.createElement('thead');const body=document.createElement('tbody');let rows=[];while(i<lines.length&&lines[i].trim().startsWith('|')){rows.push(lines[i].trim().slice(1,-1).split('|').map(x=>x.trim()));i++;}for(const [n,cells] of rows.entries()){if(n===1)continue;const tr=document.createElement('tr');for(const cell of cells){const td=document.createElement(n===0?'th':'td');td.textContent=cell;tr.append(td);} (n===0?head:body).append(tr);}table.append(head,body);const wrap=document.createElement('div');wrap.className='report-table';wrap.append(table);root.append(wrap);continue;}const p=document.createElement('p');p.textContent=line;root.append(p);i++;}}
async function showResults(){const data=await api('/api/results');const panel=$('results-panel');panel.hidden=!data.report&&!data.files.length;if(panel.hidden)return;renderReport(data.report||'尚无分析报告，点击“验收与分析”生成。');const downloads=$('result-downloads');downloads.replaceChildren();for(const name of data.files){const a=document.createElement('a');a.href='/download/'+encodeURIComponent(name);a.download=name;a.textContent=name;downloads.append(a);}}
async function render(s){const current=++epoch;state=s;ready=false;pendingBin=null;pendingRt=null;message('');$('welcome').hidden=true;$('transition').hidden=true;$('results-panel').hidden=true;$('workspace').hidden=true;$('text-form').hidden=true;$('free-text').value='';$('buttons').replaceChildren();$('pause').hidden=false;$('undo').hidden=false;$('undo').disabled=!s.can_undo;$('undo').textContent=s.needs_text?'重选本题':'回退上一题';$('identity').textContent='标注者：'+s.annotator;$('note').value=s.note||'';try{const draft=localStorage.getItem(draftKey('note',s));if(s.task_id&&draft!==null)$('note').value=draft;}catch(e){}$('note-status').textContent='提交答案或暂停时一并保存。';$('buttons').hidden=false;$('rest').hidden=!s.break_due||restDismissed;
if(['round1','scene','comparison'].includes(s.stage)){
$('workspace').hidden=false;$('right').hidden=s.stage!=='comparison';
$('text-label').textContent=s.experiment_mode==='attribution'?'其他原因（必填）':'原因（选填，不写也可以继续）';$('text-next').textContent=s.experiment_mode==='attribution'?'Enter 提交':'Enter 下一张（可留空）';
const config=s.stage==='round1'?{title:'这张图的通透感如何？',hint:'不适用：这个概念不适用于这张图。拿不准：适用，但目前无法判断。',options:[['T','通透','1'],['M','中间','2'],['N','不通透','3'],['NA','不适用','4'],['U','拿不准','5']]}:s.stage==='scene'?{title:'这张图属于哪种场景？',hint:'这一遍只记录场景类型。',options:[['户外日景','户外日景','1'],['室内人造光','室内人造光','2'],['夜景','夜景','3'],['其他','其他','4']]}:s.experiment_mode==='attribution'?{title:s.question_text,hint:'只判断差别来源，不判断哪张更好。',options:[['tone','影调','1'],['color','色彩','2'],['both','两者都有','3'],['other','其他','4'],['no_difference','看不出差别','5']]}:{title:s.question_text||'哪张图看起来更通透？',hint:'分不出高下时，选择平局。',options:[['left','左边','1'],['tie','平局','2'],['right','右边','3']]};
$('question').textContent=config.title+(s.is_review?'（浏览：可重新选择）':'');$('hint').textContent=config.hint;
$('progress').textContent=s.stage==='round1'?`粗分 ${s.round1_done} / ${s.image_count}`:s.stage==='scene'?`场景 ${s.genre_done} / ${s.image_count}`:`比较 ${s.comparison_done} / ${s.comparison_total}`;
for(const [value,label,key] of config.options){const b=document.createElement('button');b.textContent=label+'  '+key;b.dataset.value=value;b.dataset.key=key;b.disabled=true;b.onclick=()=>answer(value);$('buttons').append(b);}
try{await Promise.all([loadImage($('left'),s.image_url||s.left_url),...(s.stage==='comparison'?[loadImage($('right'),s.right_url)]:[])]);if(current!==epoch)return;sizing();await new Promise(requestAnimationFrame);started=performance.now();ready=true;disabled(false);document.activeElement.blur();if(s.needs_text){$('buttons').hidden=true;$('text-form').hidden=false;$('hint').textContent='';try{$('free-text').value=localStorage.getItem(draftKey('reason',s))||'';}catch(e){}$('free-text').focus();}}catch(e){message('图片未能加载，请暂停后重试。');}
}else{ $('transition').hidden=false;$('pause').hidden=true;$('progress').textContent='';$('transition-action').hidden=false;
if(s.stage==='prepare'){$('transition-title').textContent='粗分与场景标注已完成';$('transition-message').textContent=s.deficits.length?'题材样本不足，需要先补采并制定题库扩充记录：\n'+s.deficits.map(d=>`${d.variable} · ${d.level}：适用图片 ${d.eligible}，缺 ${d.missing}`).join('\n'):'可以生成成对比较任务。';$('transition-action').textContent='检查并生成比较任务';}
else{$('transition-title').textContent=s.stage==='finished'?'本轮标注已完成':'成对比较已完成';$('transition-message').textContent=s.experiment_mode==='attribution'?'原因归属已完成，可以导出统计。':s.experiment_mode?'比较已完成，可以按场景分析；不同场景不合并成全局量表。':'运行验收与分析，必要时生成一次定向补判。分析结果不会在作答页面显示。';$('transition-action').textContent='验收与分析';try{await showResults();}catch(e){message('读取分析结果失败：'+e.message);}}}
}
async function answer(value){if(busy||!ready)return;if(state.experiment_mode==='attribution'&&value&&value!=='other'){pendingBin=null;$('text-form').hidden=true;$('free-text').value='';}if(state.experiment_mode==='attribution'&&value==='other'&&pendingBin!=='other'){pendingBin='other';try{$('free-text').value=localStorage.getItem(draftKey('reason'))||'';}catch(e){}$('text-form').hidden=false;$('free-text').focus();return;}if(state.experiment_mode==='attribution'&&(pendingBin||value)==='other'&&!$('free-text').value.trim()){pendingBin='other';$('text-form').hidden=false;$('free-text').focus();message('请填写其他原因后按 Enter 提交。');return;}busy=true;disabled(true);try{const rt=Math.round(performance.now()-started);const payload={task_id:state.task_id,session_id:state.session_id,revision:state.revision,revise:state.is_review,note:$('note').value,rt_ms:pendingRt??rt};if(state.stage==='round1'){payload.bin=pendingBin||value;payload.free_text=$('free-text').value;}else if(state.stage==='scene')payload.scene=value;else{payload.choice=pendingBin||value;if(state.experiment_mode==='attribution')payload.attribution_text=$('free-text').value;}
const result=await api('/api/answer',payload);if(result.needs_text){state.revision=result.revision;$('undo').disabled=false;$('undo').textContent='重选本题';pendingBin=value;pendingRt=rt;$('buttons').hidden=true;$('text-form').hidden=false;$('hint').textContent='';$('free-text').focus();}else{try{localStorage.removeItem(draftKey('note'));localStorage.removeItem(draftKey('reason'));}catch(e){}$('buttons').hidden=false;await render(result);}}catch(e){message(e.message);disabled(false);}finally{busy=false;}}
$('text-form').onsubmit=e=>{e.preventDefault();answer(pendingBin);};
$('start').onclick=async()=>{if(busy)return;busy=true;try{const s=await api('/api/start',{annotator:$('annotator').value.trim()||'A'});localStorage.setItem('tongtou-name',s.annotator);sessionStarted=performance.now();restDismissed=false;$('buttons').hidden=false;await render(s);}catch(e){message(e.message);}finally{busy=false;}};
async function pause(){if(busy)return;busy=true;try{if(state.task_id)await saveNote();await api('/api/pause',{session_id:state.session_id});epoch++;ready=false;state=null;$('workspace').hidden=true;$('transition').hidden=true;$('welcome').hidden=false;$('pause').hidden=true;$('undo').hidden=true;$('rest').hidden=true;$('progress').textContent='已暂停';}catch(e){message(e.message);}finally{busy=false;}}
$('pause').onclick=pause;$('rest-pause').onclick=pause;$('rest-continue').onclick=()=>{restDismissed=true;$('rest').hidden=true;};
$('transition-action').onclick=async()=>{if(busy)return;busy=true;$('transition-action').disabled=true;try{if(state.stage==='prepare'){await api('/api/prepare',{});await render(await api('/api/start',{annotator:$('annotator').value.trim()||'A'}));}else{await api('/api/analyze',{});message('正在分析，请稍候…');const poll=setInterval(async()=>{try{const s=await api('/api/status');if(!s.job.running){clearInterval(poll);message(s.job.message);$('transition-action').disabled=false;busy=false;await render(await api('/api/start',{annotator:$('annotator').value.trim()||'A'}));message(s.job.message);}}catch(e){clearInterval(poll);message(e.message);busy=false;$('transition-action').disabled=false;}},1500);return;}}catch(e){message(e.message);}busy=false;$('transition-action').disabled=false;};
window.addEventListener('resize',sizing);
setInterval(()=>{if(state&&!restDismissed&&performance.now()-sessionStarted>=2400000)$('rest').hidden=false;},1000);
api('/api/status').then(s=>{token=s.token;$('progress').textContent=`题库 ${s.image_count} 张`;$('start').disabled=s.job.running;}).catch(e=>{message(e.message);$('start').disabled=true;});

async function saveNote(){if(!state?.task_id)return;const result=await api('/api/note',{session_id:state.session_id,task_id:state.task_id,note:$('note').value});state.revision=result.revision;$('note-status').textContent='备注已保存';}
$('save-note').onclick=async()=>{if(busy)return;busy=true;try{await saveNote();}catch(e){message(e.message);}finally{busy=false;}};
$('undo').onclick=async()=>{if(busy||!state)return;busy=true;try{if(state.task_id)await saveNote();const result=await api('/api/undo',{session_id:state.session_id,revision:state.revision});await render(result);message('已回退，请重新判断。原答案和修改记录均已保留。');}catch(e){message(e.message);}finally{busy=false;}};
try{$('annotator').value=localStorage.getItem('tongtou-name')||'A';}catch(e){}

$('note').addEventListener('input',()=>{if(!state?.task_id)return;try{localStorage.setItem(draftKey('note'),$('note').value);$('note-status').textContent='备注草稿已暂存；提交、暂停或点击保存时写入数据。';}catch(e){$('note-status').textContent='请点击保存备注。';}});

async function navigate(direction){
 if(busy||!state)return;
 if(direction===1&&!$('text-form').hidden){await answer(pendingBin);return;}
 busy=true;
 try{if(state.task_id)await saveNote();const old=state.task_id;await render(await api('/api/navigate',{session_id:state.session_id,direction}));if(direction===1&&old===state.task_id)message('请按数字键选择；选择后自动进入下一题。');}
 catch(e){message(e.message);}finally{busy=false;}
}
$('previous').onclick=()=>navigate(-1);$('next').onclick=()=>navigate(1);
$('free-text').addEventListener('input',()=>{try{localStorage.setItem(draftKey('reason'),$('free-text').value);}catch(e){}});
document.addEventListener('keydown',e=>{
 if(e.repeat||e.isComposing||e.keyCode===229||e.metaKey||e.ctrlKey||e.altKey)return;
 const active=document.activeElement,typing=['INPUT','TEXTAREA'].includes(active.tagName);
 if(typing){
  if(e.key==='Enter'&&!e.shiftKey){
   e.preventDefault();
   if(active.id==='free-text')answer(pendingBin);
   else if(active.id==='annotator')$('start').click();
   else if(active.id==='note'){$('save-note').click();active.blur();}
  }else if(e.key==='Escape'){e.preventDefault();active.blur();}
  return;
 }
 if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();navigate(e.key==='ArrowLeft'?-1:1);return;}
 if(e.key==='Enter'){
  e.preventDefault();
  if(!$('welcome').hidden)$('start').click();
  else if(!$('text-form').hidden)answer(pendingBin);
  else if(!$('transition').hidden)$('transition-action').click();
  return;
 }
 if(e.key==='Escape'&&state){e.preventDefault();pause();return;}
 if(e.key.toLowerCase()==='n'&&state?.task_id){e.preventDefault();$('note').focus();return;}
 if(e.key==='Backspace'&&state&&!$('undo').disabled){e.preventDefault();$('undo').click();return;}
 if((!$('text-form').hidden&&state?.experiment_mode!=='attribution')||$('workspace').hidden)return;
 if(state?.experiment_mode==='attribution'&&['1','2','3','5'].includes(e.key)){pendingBin=null;$('text-form').hidden=true;}
 const key=e.key===' '&&state?.stage==='comparison'&&state?.experiment_mode!=='attribution'?'2':e.key;
 const button=[...$('buttons').children].find(b=>b.dataset.key===key);
 if(button&&!button.disabled){e.preventDefault();button.click();}
});
