import webview

html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#0d1117;color:white;padding:20px;font:16px sans-serif">
<h1>JS Test</h1>
<div id="s" style="color:red">BEFORE SCRIPT</div>
<script>
document.getElementById('s').textContent='AFTER SCRIPT - JS WORKS';
document.getElementById('s').style.color='lime';
</script>
</body></html>"""

webview.create_window("JS Test", html=html, width=400, height=300)
webview.start()
