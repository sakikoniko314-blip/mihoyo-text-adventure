"""Minimal pywebview test to verify JS bridge works."""
import json
import os
import sqlite3
import webview

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "zlb.db")

class Api:
    def search(self, q, domain="", type="", category="", limit=20, offset=0):
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            like = f"%{q}%" if q else "%"
            rows = conn.execute(
                "SELECT id, doc_id, domain, doc_type, category, name FROM documents "
                "WHERE (name LIKE ? OR content LIKE ?) "
                "ORDER BY id LIMIT ? OFFSET ?",
                (like, like, min(int(limit), 50), max(0, int(offset)))
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            conn.close()
            results = [{"id": r["id"], "name": r["name"], "domain": r["domain"]} for r in rows]
            return json.dumps({"total": total, "results": results}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"total": 0, "results": [], "error": str(e)}, ensure_ascii=False)

HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Test</title>
<style>
body{background:#0d1117;color:#e6edf3;font:16px sans-serif;padding:24px}
h2{color:#7c3aed} .result{padding:8px;margin:4px 0;border:1px solid #30363d;border-radius:6px}
input{padding:8px 12px;border:1px solid #30363d;border-radius:8px;background:#161b22;color:#e6edf3;width:300px}
button{padding:8px 16px;background:#7c3aed;color:white;border:none;border-radius:8px;cursor:pointer;margin-left:8px}
#status{color:#8b949e;font-size:14px;margin:8px 0}
</style></head><body>
<h2>PyWebView Test</h2>
<div id="status">初始化...</div>
<input id="q" placeholder="搜索..." onkeydown="if(event.key==='Enter')doSearch()">
<button onclick="doSearch()">搜索</button>
<div id="results"></div>
<script>
(function(){
    var s=document.getElementById('status');
    s.textContent='JS 已加载, pywebview='+(typeof pywebview!=='undefined');

    // Retry loop for pywebview API
    var attempts=0;
    function waitForApi(){
        if(typeof pywebview!=='undefined'&&pywebview.api){
            s.textContent='API 就绪!';
        }else if(attempts<20){
            attempts++;
            setTimeout(waitForApi,200);
        }else{
            s.textContent='API 超时';
        }
    }
    setTimeout(waitForApi,100);
})();

async function doSearch(){
    var q=document.getElementById('q').value;
    var s=document.getElementById('status');
    var r=document.getElementById('results');
    s.textContent='搜索中...';
    try{
        if(typeof pywebview!=='undefined'&&pywebview.api){
            var resp=await pywebview.api.search(q);
            var data=JSON.parse(resp);
            s.textContent='找到 '+data.total+' 条 (显示 '+data.results.length+')';
            r.innerHTML=data.results.map(function(d){
                return '<div class="result"><strong>'+d.name+'</strong> ['+d.domain+']</div>';
            }).join('');
        }else{
            s.textContent='pywebview 不可用';
            r.innerHTML='<div class="result">请确保通过 pywebview 运行</div>';
        }
    }catch(e){
        s.textContent='错误: '+e.message;
        r.innerHTML='';
    }
}
</script></body></html>"""

if __name__ == "__main__":
    window = webview.create_window("Test", html=HTML, js_api=Api(), width=800, height=600)
    webview.start()
