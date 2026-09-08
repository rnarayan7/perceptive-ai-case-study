"""Local web app for labeling the harvested corpus.

A small stdlib http.server over the labeling core (:mod:`corpus.label`). One
screen: the figure image on the left, editable value rows on the right, keyboard
shortcuts to fly through them, and a progress bar. Suggestions come from the VLM
(on demand, when ``--vlm`` and a key are set) or the stored regex candidates.
Verified answers write straight to the local ``record.json``.

You stay the decider: the model only proposes; you confirm against the image.

Run (from figure-extraction/, load the key first for --vlm):
    set -a && . ./.env && set +a
    python3 -m corpus.label_server --vlm
Then open the printed http://127.0.0.1:8000 URL.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from corpus import label
from corpus.base import FigureStorage

# Server config, set in main() before serving.
STORAGE = FigureStorage()
INDEX: List[Dict[str, Any]] = []       # ordered [{id, source, type, title}]
ID_TO_SOURCE: Dict[str, str] = {}
VLM_CLIENT = None                       # a VisionClient, or None if --vlm off / no key
_CTYPE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


def build_index(source: Optional[str], figure_type: Optional[str]) -> None:
    INDEX.clear()
    ID_TO_SOURCE.clear()
    figures = STORAGE.load_figures(source=source, figure_type=figure_type)
    # Cleaner sources first: PMC figures are original rasters that read well; FDA
    # figures are rough PDF extractions the model often can't read. Ordering the
    # good ones first means suggestions work from the first figure. FDA stays
    # reachable by navigating to the end.
    figures.sort(key=lambda f: (0 if f.source == "pmc" else 1, f.source, f.figure_id))
    for fig in figures:
        INDEX.append({"id": fig.figure_id, "source": fig.source,
                      "type": fig.figure_type, "title": fig.title})
        ID_TO_SOURCE[fig.figure_id] = fig.source


def _read_record(figure_id: str) -> "Optional[tuple[dict, Path]]":
    source = ID_TO_SOURCE.get(figure_id)
    if source is None:
        return None
    directory = label.record_dir(STORAGE, source, figure_id)
    record_path = directory / "record.json"
    if not record_path.exists():
        return None
    return json.loads(record_path.read_text()), directory


def _figure_detail(figure_id: str) -> Optional[Dict[str, Any]]:
    got = _read_record(figure_id)
    if got is None:
        return None
    record, _ = got
    gt = record.get("ground_truth") or []
    return {
        "id": figure_id,
        "source": record.get("source"),
        "type": record.get("figure_type"),
        "title": record.get("title", ""),
        "context": (record.get("context") or "")[:1200],
        "image_url": f"/image?id={figure_id}",
        "rows": [{"quantity": g.get("quantity"), "value": g.get("value")}
                 for g in gt if g.get("method") == "manual"],
        "candidates": [{"quantity": g.get("quantity"), "value": g.get("value")}
                       for g in gt if g.get("method") == "regex_candidate"],
    }


def _list_payload() -> Dict[str, Any]:
    state = label.load_state()
    figures = []
    verified_total = 0
    for entry in INDEX:
        got = _read_record(entry["id"])
        verified = 0
        if got is not None:
            verified = sum(1 for g in (got[0].get("ground_truth") or [])
                           if g.get("verified"))
        verified_total += verified
        figures.append({**entry,
                        "status": (state.get(entry["id"]) or {}).get("status", "todo"),
                        "verified": verified})
    labeled = sum(1 for f in figures if f["status"] == "labeled")
    return {"figures": figures,
            "counts": {"total": len(figures), "labeled": labeled,
                       "verified_values": verified_total}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/list":
            self._json(_list_payload())
        elif path == "/api/config":
            self._json({"vlm": VLM_CLIENT is not None})
        elif path == "/api/figure":
            detail = _figure_detail((qs.get("id") or [""])[0])
            self._json(detail or {"error": "not found"}, 200 if detail else 404)
        elif path == "/api/suggest":
            self._json(self._suggest((qs.get("id") or [""])[0]))
        elif path == "/image":
            self._serve_image((qs.get("id") or [""])[0])
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/label":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        figure_id = payload.get("id", "")
        got = _read_record(figure_id)
        if got is None:
            self._json({"error": "not found"}, 404)
            return
        _, directory = got
        status = payload.get("status", "labeled")
        n = 0
        if status == "labeled":
            n = label.set_labels(directory / "record.json", payload.get("rows") or [])
        label.set_status(figure_id, status)
        self._json({"ok": True, "verified": n})

    def _suggest(self, figure_id: str) -> Dict[str, Any]:
        if VLM_CLIENT is None:
            return {"disabled": True, "rows": []}
        got = _read_record(figure_id)
        if got is None:
            return {"error": "not found", "rows": []}
        record, directory = got
        image_path = directory / f"image.{record.get('image_file', 'image.png').split('.')[-1]}"
        # image_file is like "image.png"; be robust
        for p in directory.glob("image.*"):
            image_path = p
            break
        rows = label.vlm_suggestions(image_path, record.get("figure_type", ""),
                                     record.get("context", ""), VLM_CLIENT)
        return {"rows": rows}

    def _serve_image(self, figure_id: str) -> None:
        got = _read_record(figure_id)
        if got is None:
            self._json({"error": "not found"}, 404)
            return
        _, directory = got
        images = list(directory.glob("image.*"))
        if not images:
            self._json({"error": "no image"}, 404)
            return
        ext = images[0].suffix.lstrip(".").lower()
        self._send(200, images[0].read_bytes(), _CTYPE.get(ext, "application/octet-stream"))


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Figure labeling</title>
<style>
 body{margin:0;font:14px system-ui;background:#f4f4f5;color:#18181b}
 header{padding:8px 14px;background:#fff;border-bottom:1px solid #e4e4e7;display:flex;gap:16px;align-items:center}
 #bar{height:6px;background:#e4e4e7;border-radius:3px;flex:1}#fill{height:6px;background:#16a34a;border-radius:3px;width:0}
 main{display:flex;gap:14px;padding:14px;height:calc(100vh - 120px)}
 #left{flex:1.4;display:flex;flex-direction:column;gap:6px;min-width:0}
 #ztools{display:flex;gap:6px;align-items:center;background:#fff;border:1px solid #e4e4e7;border-radius:8px;padding:6px;font-size:12px}
 .zb{border:1px solid #d4d4d8;background:#fff;border-radius:6px;padding:2px 8px;cursor:pointer;font:12px system-ui}
 #zslider{flex:1;max-width:180px}
 #imgwrap{flex:1;background:#fff;border:1px solid #e4e4e7;border-radius:8px;overflow:auto;text-align:center}
 #img{display:block;margin:0 auto;max-width:100%;cursor:zoom-in}
 #overlay{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;z-index:100;overflow:hidden;cursor:grab}
 #overlay.open{display:block}
 #oimg{position:absolute;top:0;left:0;transform-origin:0 0;user-select:none;-webkit-user-drag:none}
 #ohint{position:fixed;bottom:12px;left:50%;transform:translateX(-50%);color:#e4e4e7;font-size:12px;background:rgba(0,0,0,.5);padding:4px 10px;border-radius:6px}
 #panel{flex:1;background:#fff;border:1px solid #e4e4e7;border-radius:8px;padding:12px;overflow:auto}
 .meta{font-size:12px;color:#71717a;margin-bottom:8px}
 .ctx{font-size:12px;color:#52525b;background:#fafafa;border:1px solid #eee;border-radius:6px;padding:6px;max-height:120px;overflow:auto;margin-bottom:10px;white-space:pre-wrap}
 .row{display:flex;gap:6px;margin-bottom:6px}.row input{padding:6px;border:1px solid #d4d4d8;border-radius:6px}
 .row .q{flex:1}.row .v{flex:1}.row button{border:none;background:#fee2e2;color:#b91c1c;border-radius:6px;padding:0 8px;cursor:pointer}
 .chip{display:inline-block;background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:2px 8px;margin:2px;font-size:12px;cursor:pointer}
 footer{padding:10px 14px;background:#fff;border-top:1px solid #e4e4e7;display:flex;gap:8px;align-items:center}
 button.act{padding:8px 12px;border:1px solid #d4d4d8;background:#fff;border-radius:6px;cursor:pointer;font:14px system-ui}
 button.primary{background:#16a34a;color:#fff;border-color:#16a34a}
 .kbd{font-size:11px;color:#a1a1aa}
</style></head><body>
<header><b>Figure labeling</b><span id=pos class=meta></span><div id=bar><div id=fill></div></div>
 <span id=stats class=meta></span></header>
<main>
 <div id=left>
  <div id=ztools>
   <button class=zb onclick="setZoom(zoom-0.25)">&minus;</button>
   <input id=zslider type=range min=1 max=5 step=0.25 value=1 oninput="setZoom(this.value)">
   <button class=zb onclick="setZoom(zoom+0.25)">+</button>
   <span id=zpct>100%</span>
   <button class=zb onclick="setZoom(1)">Fit</button>
   <button class=zb onclick="openFull()">&#10530; Fullscreen (z)</button>
  </div>
  <div id=imgwrap><img id=img alt="" onclick="openFull()"></div>
 </div>
 <div id=panel>
   <div id=meta class=meta></div>
   <div id=ctx class=ctx></div>
   <div><b>Values</b> <span class=kbd>(these become verified answers)</span></div>
   <div id=rows></div>
   <button class=act onclick=addRow()>+ add value (a)</button>
   <div id=sug style="margin-top:10px"></div>
   <div id=cand style="margin-top:8px"></div>
 </div>
</main>
<footer>
 <button class="act primary" onclick=saveNext()>Save &amp; next (Enter)</button>
 <button class=act onclick=skip()>Skip (s)</button>
 <button class=act onclick=nav(-1)>&larr; prev</button>
 <button class=act onclick=nav(1)>next &rarr;</button>
 <span id=suggestbtn></span>
 <span class=kbd>Enter save · s skip · a add · &larr;/&rarr; nav</span>
</footer>
<div id=overlay><img id=oimg><div id=ohint>scroll to zoom &middot; drag to pan &middot; double-click reset &middot; Esc to close</div></div>
<script>
let figs=[], i=0, vlmOn=false, CUR={cand:[],sug:[]};
async function boot(){
  const l=await (await fetch('/api/list')).json();
  figs=l.figures; setStats(l);
  const firstTodo=figs.findIndex(f=>f.status!=='labeled'); i=firstTodo>=0?firstTodo:0;
  const cfg=await (await fetch('/api/config')).json();   // instant, no model call
  vlmOn=!!cfg.vlm;
  document.getElementById('suggestbtn').innerHTML = vlmOn?'<button class=act onclick=suggest()>Suggest (g)</button> <span class=kbd>(~10s)</span>':'';
  load();
}
function setStats(l){document.getElementById('stats').textContent=
  l.counts.labeled+'/'+l.counts.total+' labeled · '+l.counts.verified_values+' verified values';}
async function load(){
  const f=figs[i]; if(!f) return;
  document.getElementById('pos').textContent='['+(i+1)+'/'+figs.length+'] '+f.id;
  document.getElementById('fill').style.width=(100*i/figs.length)+'%';
  const d=await (await fetch('/api/figure?id='+encodeURIComponent(f.id))).json();
  document.getElementById('img').src=d.image_url+'&t='+Date.now();
  setZoom(1);
  document.getElementById('meta').textContent=d.type+' · '+(d.title||'');
  document.getElementById('ctx').textContent=d.context||'';
  setRows(d.rows.length?d.rows:[{quantity:'',value:''}]);
  CUR.cand=d.candidates||[]; CUR.sug=[];
  document.getElementById('cand').innerHTML = CUR.cand.length? '<b>auto candidates:</b> '+
     CUR.cand.map((c,k)=>'<span class=chip data-kind=cand data-k='+k+'>'+esc(c.quantity)+': '+esc(c.value)+'</span>').join(''):'';
  document.getElementById('sug').innerHTML = (vlmOn && d.rows.length)? '<span class=kbd>press g for model suggestions</span>' : '';
  if(vlmOn && d.rows.length===0) suggest();   // auto-suggest only when nothing labeled yet
}
function esc(s){return (s||'').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
function setRows(rows){const c=document.getElementById('rows');c.innerHTML='';rows.forEach(r=>addRow(r.quantity,r.value));}
function addRow(q,v){const c=document.getElementById('rows');const div=document.createElement('div');div.className='row';
  div.innerHTML='<input class=q placeholder=quantity value="'+esc(q||'')+'"><input class=v placeholder=value value="'+esc(v||'')+'"><button onclick=this.parentNode.remove()>x</button>';
  c.appendChild(div);}
function gatherRows(){return [...document.querySelectorAll('#rows .row')].map(r=>({
  quantity:r.querySelector('.q').value.trim(), value:r.querySelector('.v').value.trim()})).filter(r=>r.value);}
function fillOrAdd(q,v){const rows=[...document.querySelectorAll('#rows .row')];
  const empty=rows.find(r=>!r.querySelector('.v').value.trim());
  if(empty){empty.querySelector('.q').value=q||'';empty.querySelector('.v').value=v||'';}
  else addRow(q,v);}
document.addEventListener('click',e=>{const c=e.target.closest&&e.target.closest('.chip');if(!c)return;
  const arr=c.dataset.kind==='cand'?CUR.cand:CUR.sug;const r=arr[+c.dataset.k];if(r)fillOrAdd(r.quantity,r.value);});
async function suggest(){
  document.getElementById('sug').textContent='asking the model…';
  const s=await (await fetch('/api/suggest?id='+encodeURIComponent(figs[i].id))).json();
  CUR.sug=s.rows||[];
  document.getElementById('sug').innerHTML = CUR.sug.length?
    '<b>model suggests:</b> '+CUR.sug.map((r,k)=>'<span class=chip data-kind=sug data-k='+k+'>'+esc(r.quantity)+': '+esc(r.value)+'</span>').join(''):'<span class=kbd>no model suggestions</span>';
}
async function post(status){await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({id:figs[i].id,status:status,rows:status==='labeled'?gatherRows():[]})});
  figs[i].status=status;}
async function saveNext(){await post('labeled');nav(1);refreshStats();}
async function skip(){await post('skipped');nav(1);}
function nav(d){i=Math.max(0,Math.min(figs.length-1,i+d));load();}
async function refreshStats(){const l=await (await fetch('/api/list')).json();
  document.getElementById('stats').textContent=l.counts.labeled+'/'+l.counts.total+' labeled · '+l.counts.verified_values+' verified values';}
// --- inline zoom (slider / buttons; wheel still scrolls the panel) ---
let zoom=1;
function setZoom(z){ zoom=Math.max(1,Math.min(5,parseFloat(z)||1));
  const img=document.getElementById('img'); img.style.maxWidth='none'; img.style.width=(zoom*100)+'%';
  document.getElementById('zpct').textContent=Math.round(zoom*100)+'%';
  document.getElementById('zslider').value=zoom; }
// --- fullscreen overlay (wheel = zoom to cursor, drag = pan) ---
let os=1,otx=0,oty=0,onatW=0,onatH=0,odrag=false,osx=0,osy=0;
function openFull(){ const oimg=document.getElementById('oimg'); oimg.src=document.getElementById('img').src;
  document.getElementById('overlay').classList.add('open');
  if(oimg.complete&&oimg.naturalWidth) fitFull(); else oimg.onload=fitFull; }
function fitFull(){ const oimg=document.getElementById('oimg'); onatW=oimg.naturalWidth||1; onatH=oimg.naturalHeight||1;
  os=Math.min(window.innerWidth/onatW, window.innerHeight/onatH)*0.95;
  otx=(window.innerWidth-onatW*os)/2; oty=(window.innerHeight-onatH*os)/2; applyFull(); }
function applyFull(){ const o=document.getElementById('oimg'); o.style.width=onatW+'px';
  o.style.transform='translate('+otx+'px,'+oty+'px) scale('+os+')'; }
function closeFull(){ document.getElementById('overlay').classList.remove('open'); }
(function(){ const ov=document.getElementById('overlay');
  ov.addEventListener('wheel',e=>{ e.preventDefault(); const f=e.deltaY<0?1.15:1/1.15;
    const ix=(e.clientX-otx)/os, iy=(e.clientY-oty)/os; os=Math.max(0.05,Math.min(25,os*f));
    otx=e.clientX-ix*os; oty=e.clientY-iy*os; applyFull(); }, {passive:false});
  ov.addEventListener('mousedown',e=>{ if(e.target.id==='oimg'){ odrag=true; osx=e.clientX-otx; osy=e.clientY-oty; ov.style.cursor='grabbing'; e.preventDefault(); } });
  window.addEventListener('mousemove',e=>{ if(odrag){ otx=e.clientX-osx; oty=e.clientY-osy; applyFull(); } });
  window.addEventListener('mouseup',()=>{ odrag=false; ov.style.cursor='grab'; });
  ov.addEventListener('dblclick',fitFull);
  ov.addEventListener('click',e=>{ if(e.target.id==='overlay') closeFull(); });
})();
document.addEventListener('keydown',e=>{
  if(document.getElementById('overlay').classList.contains('open')){ if(e.key==='Escape') closeFull(); return; }
  const t=e.target.tagName;
  if(t==='INPUT'){ if(e.key==='Enter'&&e.ctrlKey) saveNext(); return; }
  if(e.key==='Enter') saveNext();
  else if(e.key==='s') skip();
  else if(e.key==='a'){e.preventDefault();addRow();}
  else if(e.key==='g'&&vlmOn) suggest();
  else if(e.key==='z') openFull();
  else if(e.key==='ArrowRight') nav(1);
  else if(e.key==='ArrowLeft') nav(-1);
});
boot();
</script></body></html>"""


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="corpus.label_server", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--source", help="only this source")
    p.add_argument("--type", help="only this figure type")
    p.add_argument("--vlm", action="store_true", help="enable VLM suggestions (needs ANTHROPIC_API_KEY)")
    p.add_argument("--vlm-model", default="claude-sonnet-5")
    args = p.parse_args(argv)

    build_index(args.source, args.type)
    if not INDEX:
        print("No figures on disk to label.")
        return 0

    global VLM_CLIENT
    if args.vlm:
        from evaluation.llm import AnthropicClient
        client = AnthropicClient(model=args.vlm_model)
        if client.available():
            VLM_CLIENT = client
        else:
            print("--vlm set but ANTHROPIC_API_KEY missing; suggestions disabled.")

    print(f"Labeling {len(INDEX)} figures. Open http://{args.host}:{args.port}  (Ctrl-C to stop)")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
