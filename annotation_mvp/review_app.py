#!/usr/bin/env python3
"""Local review UI for a variable-size boundary-image candidate pool."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


STRATA = {
    "unassigned": "未预分层（本轮未使用FADE）",
    "obvious_limpid": "明显通透",
    "obvious_dull": "明显灰闷",
    "real_fog_beautiful_layers": "有真实雾但层次漂亮",
    "no_fog_but_gray": "没有雾但发灰",
    "high_contrast_overprocessed": "高对比但处理过度",
    "soft_low_contrast_clean": "柔和低对比但干净",
}

SCENES = {
    "portrait": "人像",
    "landscape": "风景",
    "city_street": "城市街景",
    "architecture": "建筑",
    "interior": "室内",
    "still_life": "静物",
    "flora": "花卉植物",
    "animal": "动物",
    "food": "食物",
    "art_graphic": "艺术图形",
    "night": "夜景",
    "mixed": "混合场景",
    "other": "其他",
    "undecidable": "无法判断",
}

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash-vision-exp"

THEME_SCENE = {
    "portrait": "portrait", "people": "portrait", "family": "portrait",
    "nature": "landscape", "scene": "landscape", "park": "landscape",
    "sky": "landscape", "clouds": "landscape", "water": "landscape",
    "river": "landscape", "lake": "landscape", "sea": "landscape",
    "beach": "landscape", "snow": "landscape", "sunset": "landscape",
    "city": "city_street", "citysky": "city_street", "street": "city_street",
    "bridge": "architecture", "house": "architecture",
    "flower": "flora", "food": "food", "macro": "still_life",
    "art": "art_graphic", "night": "night",
}

PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>第二轮候选审核</title>
<style>
:root{--bg:#f4f4ef;--paper:#fff;--ink:#26302c;--muted:#747c76;--green:#315d47;--red:#8c4938;--line:#dfe3db}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
header{height:72px;padding:0 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);background:#fafaf7}
header b{font-size:20px}.muted{color:var(--muted)}main{max-width:1180px;margin:auto;padding:22px}.top{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:14px}
progress{width:280px;height:9px}.stage{height:58vh;min-height:380px;background:#747474;border-radius:10px;display:flex;align-items:center;justify-content:center;overflow:hidden}.stage img{width:100%;height:100%;object-fit:contain}
.meta{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;margin:14px 0;gap:10px}.meta div{background:var(--paper);border:1px solid var(--line);padding:12px;border-radius:8px}.meta span{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.actions button,.actions select{padding:12px 16px;border:1px solid var(--line);border-radius:8px;background:white}.actions button{cursor:pointer}.keep{background:var(--green)!important;color:white}.drop{color:var(--red)}textarea{width:100%;margin-top:12px;min-height:65px;padding:12px;border:1px solid var(--line);border-radius:8px;font:inherit}
.scene-editor{display:flex;align-items:center;gap:10px;margin:12px 0;padding:12px;background:var(--paper);border:1px solid var(--line);border-radius:8px}.scene-editor select{padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:white}.unconfirmed{color:var(--red)}
#msg{min-height:22px;margin-top:10px;color:var(--green)}kbd{border:1px solid #bbb;border-radius:4px;padding:1px 5px;font-size:11px}.nav{margin-left:auto}.nav button{padding:9px 13px}
@media(max-width:700px){main{padding:12px}.stage{height:48vh}.meta{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><header><b>通透候选图片审核</b><span class="muted">候选分层不是通透标签</span></header><main>
<div class="top"><div><strong id="id">—</strong> <span id="count" class="muted"></span><div id="progressText" class="muted"></div></div><div><label for="target">目标图片数 </label><input id="target" type="number" min="1" step="1" style="width:90px;padding:8px"><button id="saveTarget" style="padding:8px">保存</button></div><progress id="progress" max="200" value="0"></progress></div>
<div class="stage"><img id="image" alt="待审核候选图片"></div>
<div class="meta"><div><span>TAD66K 原始主题</span><b id="theme"></b></div><div><span>DeepSeek 建议场景，仅供参考</span><b id="suggestedScene"></b></div><div><span>机器候选分层</span><b id="stratum"></b></div><div><span>代理指标，仅供审计</span><b id="metrics"></b></div></div>
<div class="scene-editor"><label for="reviewedScene"><b>人工确认场景</b></label><select id="reviewedScene"></select><span id="sceneStatus" class="unconfirmed">尚未人工确认</span></div>
<div class="actions"><button id="keep" class="keep">保留 <kbd>K</kbd></button><button id="drop" class="drop">排除 <kbd>X</kbd></button><select id="move"></select><button id="moveSave">移入所选分类 <kbd>M</kbd></button><div class="nav"><button id="prev">← 上一张</button><button id="next">下一张 →</button></div></div>
<textarea id="note" maxlength="2000" placeholder="可记录保留、移类或排除理由（选填）"></textarea><div id="msg"></div>
</main><script>
'use strict';let token='',rows=[],index=0,decisions={},targetCount=200,sceneNames={},suggestionAttempts=new Set(),sceneDirty=false;
const $=id=>document.getElementById(id);const names={unassigned:'未预分层（本轮未使用FADE）',obvious_limpid:'明显通透',obvious_dull:'明显灰闷',real_fog_beautiful_layers:'有真实雾但层次漂亮',no_fog_but_gray:'没有雾但发灰',high_contrast_overprocessed:'高对比但处理过度',soft_low_contrast_clean:'柔和低对比但干净'};
async function api(path,data){const r=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(data)}:{});const j=await r.json();if(!r.ok)throw new Error(j.error||'请求失败');return j}
function metric(v,digits){if(v===null||v===undefined||v==='')return '—';const n=Number(v);return Number.isFinite(n)?n.toFixed(digits):'—'}
function metricSummary(r){const parts=[],fade=metric(r.fade_density,3),aesthetic=metric(r.aesthetic_score,2),contrast=metric(r.robust_contrast,3);if(fade!=='—')parts.push(`FADE* ${fade}`);if(aesthetic!=='—')parts.push(`美学 ${aesthetic}`);if(contrast!=='—')parts.push(`对比 ${contrast}`);return parts.join(' · ')||'—'}
function sceneLabel(value){return `${sceneNames[value]||'其他'}（${value||'other'}）`}
function updateSceneStatus(){const r=rows[index],selected=$('reviewedScene').value;if(selected!==r.scene_suggested){$('sceneStatus').textContent=sceneDirty?'人工改选，待随审核决定保存':'已人工改选';$('sceneStatus').className='unconfirmed'}else if(r.scene_suggestion_source==='deepseek'){$('sceneStatus').textContent='默认采用 DeepSeek 建议';$('sceneStatus').className=''}else{$('sceneStatus').textContent='等待 DeepSeek 建议';$('sceneStatus').className='unconfirmed'}}
async function refreshSuggestion(r){if(r.scene_suggestion_source==='deepseek'||suggestionAttempts.has(r.preview_id))return;suggestionAttempts.add(r.preview_id);$('msg').textContent='DeepSeek 正在判断场景…';try{const preserve=sceneDirty?$('reviewedScene').value:'';const s=await api('/api/suggest-scene',{preview_id:r.preview_id});Object.assign(r,s.row);if(s.decision)decisions[r.preview_id]=s.decision;if(rows[index].preview_id===r.preview_id){render();if(preserve){$('reviewedScene').value=preserve;sceneDirty=true;updateSceneStatus()}$('msg').textContent=`DeepSeek 建议：${sceneLabel(r.scene_suggested)}`}}catch(e){if(rows[index].preview_id===r.preview_id)$('msg').textContent=e.message}}
function render(){const r=rows[index],d=decisions[r.preview_id]||{};$('id').textContent=r.preview_id;$('count').textContent=`${index+1} / ${rows.length}`;$('image').src='/image/'+r.preview_id;$('theme').textContent=r.theme_original||'来源主题缺失';$('suggestedScene').textContent=r.scene_suggestion_source==='deepseek'?sceneLabel(r.scene_suggested):`等待 DeepSeek（当前暂用 ${sceneLabel(r.scene_suggested)}）`;$('stratum').textContent=names[r.candidate_stratum]||r.candidate_stratum;$('metrics').textContent=metricSummary(r);$('reviewedScene').value=d.scene_reviewed||r.scene_suggested||'other';sceneDirty=false;updateSceneStatus();$('move').value=d.move_to||r.candidate_stratum;$('note').value=d.note||'';const done=Object.values(decisions).filter(x=>x.decision&&x.decision!=='pending').length;const selected=Object.values(decisions).filter(x=>x.decision==='keep'||x.decision==='move').length;$('progress').max=targetCount;$('progress').value=Math.min(selected,targetCount);$('target').max=rows.length;$('target').value=targetCount;$('progressText').textContent=`已审核 ${done} / ${rows.length} · 已选 ${selected} / 目标 ${targetCount} · 还差 ${Math.max(0,targetCount-selected)} · 当前：${d.decision||'pending'}`;$('prev').disabled=index===0;$('next').disabled=index===rows.length-1;refreshSuggestion(r);}
async function decide(decision,move_to=''){const r=rows[index];try{const sceneReviewed=decision==='drop'?'':$('reviewedScene').value;const s=await api('/api/decision',{preview_id:r.preview_id,decision,move_to,scene_reviewed:sceneReviewed,note:$('note').value});decisions=s.decisions;$('msg').textContent='已保存';if(index<rows.length-1)index++;render();}catch(e){$('msg').textContent=e.message}}
async function start(){const s=await api('/api/status');token=s.token;rows=s.rows;decisions=s.decisions;targetCount=s.target_count;sceneNames=s.scene_names;for(const [value,text] of Object.entries(names)){const o=document.createElement('option');o.value=value;o.textContent=text;$('move').append(o)};for(const [value,text] of Object.entries(sceneNames)){const o=document.createElement('option');o.value=value;o.textContent=`${text}（${value}）`;$('reviewedScene').append(o)};render()}
$('keep').onclick=()=>decide('keep');$('drop').onclick=()=>decide('drop');$('moveSave').onclick=()=>decide('move',$('move').value);$('prev').onclick=()=>{if(index){index--;render()}};$('next').onclick=()=>{if(index<rows.length-1){index++;render()}};$('saveTarget').onclick=async()=>{try{const s=await api('/api/settings',{target_count:Number($('target').value)});targetCount=s.target_count;$('msg').textContent='目标数量已保存';render()}catch(e){$('msg').textContent=e.message}};
document.addEventListener('keydown',e=>{if(document.activeElement.tagName==='TEXTAREA'||document.activeElement.tagName==='SELECT')return;if(e.key.toLowerCase()==='k')decide('keep');else if(e.key.toLowerCase()==='x')decide('drop');else if(e.key.toLowerCase()==='m')decide('move',$('move').value);else if(e.key==='ArrowLeft')$('prev').click();else if(e.key==='ArrowRight')$('next').click()});start().catch(e=>$('msg').textContent=e.message);
$('reviewedScene').onchange=()=>{sceneDirty=true;updateSceneStatus()};
</script></body></html>'''


class SceneSuggestionError(RuntimeError):
    pass


class DeepSeekSceneClient:
    def __init__(self, api_key: str, base_url: str = DEEPSEEK_BASE_URL, model: str = DEEPSEEK_MODEL):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @staticmethod
    def normalize(content: str) -> str:
        lowered = (content or "").strip().lower().replace("-", "_")
        for scene in sorted(SCENES, key=len, reverse=True):
            if re.search(rf"(?<![a-z_]){re.escape(scene)}(?![a-z_])", lowered):
                return scene
        for scene, chinese in sorted(SCENES.items(), key=lambda item: len(item[1]), reverse=True):
            if chinese in content:
                return scene
        raise SceneSuggestionError("模型没有返回可识别的场景名词，请使用人工下拉框")

    def suggest(self, image_path: Path) -> str:
        try:
            from openai import OpenAI

            mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            allowed = ", ".join(SCENES)
            client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=60.0)
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"这张图的主要场景是什么？只回答以下英文场景名词之一，不要解释：{allowed}"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ],
                }],
                max_tokens=24,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return self.normalize(response.choices[0].message.content or "")
        except SceneSuggestionError:
            raise
        except Exception as exc:
            message = str(exc).strip().replace(self.api_key, "***")
            raise SceneSuggestionError(f"DeepSeek 场景识别失败：{message[:300]}") from exc


class ReviewStore:
    def __init__(self, batch: Path, selection: Path | None = None, decisions: Path | None = None, target_count: int = 200):
        self.batch = batch.resolve()
        self.selection = (selection or self.batch / "preview_selection_200.csv").resolve()
        self.decisions_path = (decisions or self.batch / "review_decisions.csv").resolve()
        self.settings_path = self.decisions_path.with_name(self.decisions_path.stem + "_settings.json")
        self.lock = threading.Lock()
        with self.selection.open(encoding="utf-8-sig", newline="") as handle:
            self.rows = list(csv.DictReader(handle))
        for row in self.rows:
            row["preview_id"] = row.get("preview_id") or row.get("candidate_id", "")
            row["preview_path"] = row.get("preview_path") or row.get("path", "")
            row["theme_original"] = row.get("theme_original") or row.get("theme", "")
            row["scene_suggested"] = THEME_SCENE.get(row["theme_original"], "other")
            row["scene_suggestion_source"] = "theme_map"
        self.rows.sort(key=lambda row: (row["theme_original"], row["preview_id"]))
        self.by_id = {row["preview_id"]: row for row in self.rows}
        if not self.rows or len(self.by_id) != len(self.rows) or "" in self.by_id:
            raise ValueError("审核清单的图片 ID 必须存在且唯一")
        self.decisions = self._read_decisions()
        for row in self.rows:
            saved = self.decisions.get(row["preview_id"], {})
            if saved.get("scene_suggestion_source") == "deepseek" and saved.get("scene_suggested") in SCENES:
                row["scene_suggested"] = saved["scene_suggested"]
                row["scene_suggestion_source"] = "deepseek"
        self.target_count = min(target_count, len(self.rows))
        if self.settings_path.exists():
            saved = json.loads(self.settings_path.read_text())
            saved_target = int(saved.get("target_count", self.target_count))
            if 1 <= saved_target <= len(self.rows):
                self.target_count = saved_target

    def _read_decisions(self) -> dict[str, dict]:
        if not self.decisions_path.exists():
            return {}
        with self.decisions_path.open(encoding="utf-8-sig", newline="") as handle:
            return {row["preview_id"]: row for row in csv.DictReader(handle)}

    def _write(self) -> None:
        fields = ["preview_id", "theme_original", "scene_suggested", "scene_suggestion_source", "scene_reviewed", "candidate_stratum", "decision", "move_to", "note"]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=self.batch, delete=False) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in self.rows:
                current = self.decisions.get(row["preview_id"], {})
                writer.writerow({
                    "preview_id": row["preview_id"],
                    "theme_original": row["theme_original"],
                    "scene_suggested": row["scene_suggested"],
                    "scene_suggestion_source": row["scene_suggestion_source"],
                    "scene_reviewed": current.get("scene_reviewed", ""),
                    "candidate_stratum": row["candidate_stratum"],
                    "decision": current.get("decision", "pending"),
                    "move_to": current.get("move_to", ""),
                    "note": current.get("note", ""),
                })
            temp = Path(handle.name)
        temp.replace(self.decisions_path)

    def decide(self, data: dict) -> None:
        preview_id = data.get("preview_id")
        decision = data.get("decision")
        move_to = data.get("move_to", "")
        scene_reviewed = data.get("scene_reviewed", "")
        note = data.get("note", "")
        if preview_id not in self.by_id:
            raise ValueError("候选图片不存在")
        if decision not in {"keep", "drop", "move"}:
            raise ValueError("审核决定不合法")
        if decision == "move" and move_to not in STRATA:
            raise ValueError("目标分类不合法")
        if scene_reviewed and scene_reviewed not in SCENES:
            raise ValueError("人工场景不合法")
        if decision in {"keep", "move"} and scene_reviewed not in SCENES:
            raise ValueError("保留或移类前，请确认人工场景")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("备注最多 2000 字")
        with self.lock:
            row = self.by_id[preview_id]
            self.decisions[preview_id] = {
                "preview_id": preview_id,
                "theme_original": row["theme_original"],
                "scene_suggested": row["scene_suggested"],
                "scene_suggestion_source": row["scene_suggestion_source"],
                "scene_reviewed": scene_reviewed if decision != "drop" else "",
                "candidate_stratum": row["candidate_stratum"],
                "decision": decision,
                "move_to": move_to if decision == "move" else "",
                "note": note.strip(),
            }
            self._write()

    def set_scene_suggestion(self, preview_id: str, scene: str) -> dict:
        if preview_id not in self.by_id:
            raise ValueError("候选图片不存在")
        if scene not in SCENES:
            raise ValueError("模型场景不合法")
        with self.lock:
            row = self.by_id[preview_id]
            previous_suggestion = row["scene_suggested"]
            previous_source = row["scene_suggestion_source"]
            current = self.decisions.get(preview_id)
            if (
                current is not None
                and previous_source != "deepseek"
                and current.get("scene_reviewed") == previous_suggestion
            ):
                current["scene_reviewed"] = scene
            row["scene_suggested"] = scene
            row["scene_suggestion_source"] = "deepseek"
            if current is not None:
                current["scene_suggested"] = scene
                current["scene_suggestion_source"] = "deepseek"
            self._write()
            return row.copy()

    def set_target(self, value) -> int:
        if type(value) is not int or not 1 <= value <= len(self.rows):
            raise ValueError(f"目标图片数须在 1–{len(self.rows)} 之间")
        with self.lock:
            self.target_count = value
            self.settings_path.write_text(json.dumps({"target_count": value}, ensure_ascii=False, indent=2))
        return value


def handler_for(store: ReviewStore, token: str, scene_client: DeepSeekSceneClient | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_data(self, status: int, payload, content_type="application/json; charset=utf-8"):
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False).encode()
            elif isinstance(payload, str):
                payload = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self.send_data(200, PAGE, "text/html; charset=utf-8")
            if path == "/api/status":
                return self.send_data(200, {"token": token, "rows": store.rows, "decisions": store.decisions, "target_count": store.target_count, "scene_names": SCENES})
            if path.startswith("/image/"):
                preview_id = path.split("/")[-1]
                row = store.by_id.get(preview_id)
                if not row:
                    return self.send_data(404, {"error": "图片不存在"})
                image = Path(row["preview_path"]).resolve()
                if not image.is_file():
                    return self.send_data(404, {"error": "图片文件不存在"})
                return self.send_data(200, image.read_bytes(), mimetypes.guess_type(image.name)[0] or "image/jpeg")
            return self.send_data(404, {"error": "页面不存在"})

        def do_POST(self):
            if self.headers.get("X-Review-Token") != token:
                return self.send_data(403, {"error": "请刷新页面后重试"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 16384:
                    raise ValueError("请求大小不合法")
                data = json.loads(self.rfile.read(size))
                if self.path == "/api/decision":
                    store.decide(data)
                    return self.send_data(200, {"decisions": store.decisions})
                if self.path == "/api/settings":
                    return self.send_data(200, {"target_count": store.set_target(data.get("target_count"))})
                if self.path == "/api/suggest-scene":
                    if scene_client is None:
                        return self.send_data(503, {"error": "未检测到 DEEPSEEK_API_KEY；请设置环境变量后重新启动审核台"})
                    preview_id = data.get("preview_id", "")
                    row = store.by_id.get(preview_id)
                    if row is None:
                        raise ValueError("候选图片不存在")
                    image = Path(row["preview_path"]).resolve()
                    if not image.is_file():
                        raise ValueError("图片文件不存在")
                    try:
                        scene = scene_client.suggest(image)
                    except SceneSuggestionError as exc:
                        return self.send_data(502, {"error": str(exc)})
                    updated = store.set_scene_suggestion(preview_id, scene)
                    return self.send_data(200, {"row": updated, "decision": store.decisions.get(preview_id)})
                return self.send_data(404, {"error": "接口不存在"})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return self.send_data(400, {"error": str(exc)})
    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--target-count", type=int, default=200)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    store = ReviewStore(args.batch, args.selection, args.decisions, args.target_count)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    scene_client = DeepSeekSceneClient(api_key) if api_key else None
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(store, secrets.token_urlsafe(24), scene_client))
    print(f"候选审核台：http://127.0.0.1:{args.port}\n审核目录：{store.batch}", flush=True)
    if scene_client is None:
        print("提示：未检测到 DEEPSEEK_API_KEY，页面仍可人工审核，但不会自动生成 DeepSeek 场景建议。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
