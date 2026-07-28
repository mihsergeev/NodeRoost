# Картинка-превью для GitHub (1280×640). Сам файл GitHub не подхватывает —
# его загружают руками в Settings → Social preview; в репозитории он лежит,
# чтобы было что загрузить и чем заменить при ребрендинге.
import subprocess
from pathlib import Path

ROOT = Path(r"Z:\ms\NodeRoost")
OUT = ROOT / "docs" / "social-preview.png"
TMP = Path(r"C:\Users\Msergeev\AppData\Local\Temp\nr-social.html")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
LOGO = str(ROOT / "landing" / "site" / "logo.svg").replace("\\", "/")

HTML = f"""<!doctype html><meta charset="utf-8"><style>
  html,body{{margin:0;width:1280px;height:640px;overflow:hidden}}
  body{{
    background:#0D111A; color:#F4F7FA;
    font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    text-align:center; padding:0 90px; box-sizing:border-box; position:relative;
  }}
  body::before{{
    content:""; position:absolute; top:-42%; left:50%; width:1100px; height:1100px;
    transform:translateX(-50%);
    background:radial-gradient(circle, rgba(0,229,138,.15), transparent 62%);
  }}
  .in{{position:relative}}
  img{{width:620px; display:block; margin:0 auto 40px}}
  h1{{font-size:40px; margin:0 0 18px; letter-spacing:-.02em; font-weight:650}}
  .hl{{color:#00E58A}}
  p{{font-size:22px; color:#9AA6B8; margin:0 0 34px; line-height:1.45}}
  .row{{display:flex; gap:10px; justify-content:center; flex-wrap:wrap}}
  span.chip{{
    border:1px solid #262D3B; border-radius:999px; padding:7px 16px;
    font-size:16px; color:#C6D0DE;
  }}
</style>
<div class="in">
  <img src="file:///{LOGO}">
  <h1>Self-hosted panel for <span class="hl">headscale</span></h1>
  <p>Servers, devices, access grants and routes — instead of hand-written HuJSON</p>
  <div class="row">
    <span class="chip">device isolation</span>
    <span class="chip">exit gateways</span>
    <span class="chip">routing</span>
    <span class="chip">backups &amp; alerts</span>
    <span class="chip">BSD-3-Clause</span>
  </div>
</div>"""

TMP.write_text(HTML, encoding="utf-8")
subprocess.run([
    CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
    "--window-size=1280,640", "--force-device-scale-factor=1",
    "--default-background-color=0D111A",
    f"--screenshot={OUT}", TMP.as_uri(),
], check=True, capture_output=True, timeout=120)
print(f"{OUT.name}: {OUT.stat().st_size // 1024} КБ")
