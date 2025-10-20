# modules/presenca/routes.py
import re
import io
import csv
import datetime as dt
from flask import Blueprint, request, jsonify, render_template_string, Response, current_app
from models import db, Matricula, Presenca

presenca_bp = Blueprint("presenca", __name__)
FORMAT = re.compile(r"^MR\d{5}$")  # padrão MR + 5 dígitos

# ------------------ Página HTML (formulário simples) ------------------
PAGE = """
<!doctype html><meta charset="utf-8">
<title>Confirmação de Presença</title>
<style>
  body{font-family:system-ui;background:#0f172a;color:#e5e7eb;margin:0;
       display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:24px;
        width:92%;max-width:500px;box-shadow:0 16px 40px rgba(0,0,0,.35)}
  input{width:100%;padding:10px;border-radius:8px;border:1px solid #374151;
        background:#0b1222;color:#e5e7eb;margin-bottom:10px}
  button{width:100%;padding:10px;border:0;border-radius:8px;background:#10b981;
         color:#052e24;font-weight:700;cursor:pointer}
  .msg{margin-top:16px;padding:10px;border-radius:10px;text-align:center}
  .ok{background:#064e3b;color:#a7f3d0}
  .err{background:#7f1d1d;color:#fecaca}
  .warn{background:#78350f;color:#fde68a}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
</style>

<div class="card">
  <h1>Confirmação de Presença</h1>
  <form method="get" action="/presenca">
    <input name="matricula" placeholder="Digite sua matrícula (ex: MR25684)" value="{{ matricula or '' }}">
    <button>Confirmar Presença</button>
  </form>

  {% if tried %}
    {% if ok %}
      {% if already %}
        <div class="msg warn">⚠️ Presença já confirmada hoje<br><b>{{ code }}</b></div>
      {% else %}
        <div class="msg ok">✅ Presença registrada com sucesso!<br><b>{{ code }}</b></div>
      {% endif %}
    {% else %}
      <div class="msg err">❌ {{ msg }}</div>
    {% endif %}
  {% endif %}
</div>
"""

# ------------------ Página de SUCESSO com animações ------------------
PAGE_SUCCESS_PRESENCA = """
<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Presença confirmada</title>
<style>
  :root{
    --bg: #231f20;
    --card: #2e2b2c;
    --line: #3a3536;
    --text: #f4f4f4;
    --ok: #7a1315;
    --accent: #d1a34a;
  }
  html,body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,Segoe UI,Roboto,Arial}
  .wrap{min-height:100vh;display:flex;flex-direction:column;gap:18px;align-items:center;justify-content:center;padding:24px}
  .top{display:flex;flex-direction:column;align-items:center;gap:8px}
  .logo{height:40px;margin-bottom:4px}
  h1{margin:0 0 4px;font-size:1.5rem;text-align:center}
  .sub{opacity:.9;margin:2px 0 8px;text-align:center}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
  .pill{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;border:1px solid var(--line);background:#2e2b2c;font-weight:700}
  .ok{color:var(--ok)}
  .grid{display:grid;grid-template-columns:repeat(2,minmax(260px,320px));gap:18px;margin-top:8px}
  @media (max-width:720px){.grid{grid-template-columns:1fr}}
  .card{
    background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:0 12px 32px rgba(0,0,0,.35);
    display:flex;flex-direction:column;gap:10px; align-items:center; justify-content:center; min-height:260px; position:relative; overflow:hidden;
  }
  .card h2{margin:0;font-size:1rem;text-align:center}
  .hint{opacity:.75;font-size:.9rem;text-align:center}
  .anim { animation-play-state: paused; }
  .card:hover .anim { animation-play-state: running; }
  @media (prefers-reduced-motion: reduce){ .anim { animation: none !important; } }

  /* Avião */
  .globe{width:180px;height:180px; position:relative}
  .earth{fill:#6b7280}.earth-land{fill:var(--ok); opacity:.85}
  .plane{width:28px;height:28px; position:absolute; top:50%; left:50%; transform-origin:-60px -60px;}
  .orbit.anim{ animation: orbit 4.5s linear infinite; }
  @keyframes orbit { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

  /* Formatura */
  .grad{width:200px;height:140px; position:relative}
  .cap{width:120px;height:60px; background:#111; border:2px solid #333; transform: skewX(-10deg) rotate(-8deg); margin:auto; border-radius:4px; position:relative;}
  .cap:after{content:""; position:absolute; right:-6px; top:18px; width:2px; height:42px; background:var(--accent);}
  .confetti{position:absolute; inset:0; pointer-events:none}
  .confetti span{position:absolute; width:8px; height:12px; opacity:0; border-radius:2px;}
  .confetti span:nth-child(1){left:10%; background:#ef4444; animation: drop 1.2s ease-in infinite; }
  .confetti span:nth-child(2){left:22%; background:#22c55e; animation: drop 1.3s .1s ease-in infinite; }
  .confetti span:nth-child(3){left:34%; background:#3b82f6; animation: drop 1.1s .2s ease-in infinite; }
  .confetti span:nth-child(4){left:46%; background:var(--accent); animation: drop 1.4s .15s ease-in infinite; }
  .confetti span:nth-child(5){left:58%; background:#a855f7; animation: drop 1.25s .05s ease-in infinite; }
  .confetti span:nth-child(6){left:70%; background:#f97316; animation: drop 1.3s .2s ease-in infinite; }
  .confetti span:nth-child(7){left:82%; background:#06b6d4; animation: drop 1.15s .25s ease-in infinite; }
  .confetti span:nth-child(8){left:15%; background:#f43f5e; animation: drop 1.35s .05s ease-in infinite; }
  .confetti span:nth-child(9){left:55%; background:#84cc16; animation: drop 1.1s .18s ease-in infinite; }
  .confetti span:nth-child(10){left:75%; background:#e879f9; animation: drop 1.5s .22s ease-in infinite; }
  .confetti .anim{ animation-play-state: paused; }
  @keyframes drop{ 0% { transform: translateY(-20px) rotate(0); opacity:0; } 10% { opacity:1; } 100% { transform: translateY(140px) rotate(240deg); opacity:0; } }

  /* Carro */
  .road{width:260px;height:90px; background:linear-gradient(#1b1b1b,#0f0f0f); border-radius:10px; position:relative; overflow:hidden; border:1px solid var(--line)}
  .lane{position:absolute; left:0; right:0; height:4px; top:50%; background:repeating-linear-gradient(90deg, transparent 0 26px, #f4f4f4 26px 52px); opacity:.7}
  .car{position:absolute; bottom:12px; left:-120px; width:110px; height:44px; background:var(--ok); border:2px solid #501010; border-radius:8px; box-shadow:inset 0 -6px 0 rgba(0,0,0,.15)}
  .car:before{content:""; position:absolute; left:8px; top:-14px; width:46px; height:28px; background:#9a2a2d; border:2px solid #501010; border-radius:8px}
  .win{position:absolute; left:24px; top:-8px; width:22px; height:16px; background:#ddd; border-radius:3px}
  .wheel{position:absolute; bottom:-10px; width:24px; height:24px; border-radius:50%; background:radial-gradient(circle at 50% 50%, #444 0 45%, #111 46% 100%); border:2px solid #000}
  .w1{left:14px}.w2{right:16px}
  .drive.anim{ animation: drive 2.2s ease-in-out infinite; }
  .spin.anim{ animation: spin .7s linear infinite; }
  @keyframes drive { 0% { transform: translateX(0); } 60% { transform: translateX(330px); } 100% { transform: translateX(460px); } }
  @keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

  /* Pessoa */
  .yard{width:240px;height:130px; position:relative; background:linear-gradient(#2b2727,#1f1c1c); border:1px solid var(--line); border-radius:10px; overflow:hidden}
  .house{position:absolute; right:24px; bottom:16px; width:90px; height:70px; background:#383434; border:2px solid #111; border-radius:6px}
  .roof{position:absolute; right:22px; bottom:84px; width:96px; height:24px; background:#111; clip-path:polygon(50% 0,100% 100%,0 100%)}
  .door{position:absolute; right:44px; bottom:16px; width:30px; height:48px; background:#0b1222; border:2px solid #0f172a}
  .person{position:absolute; left:10px; bottom:18px; width:20px; height:42px}
  .head{width:18px; height:18px; border-radius:50%; background:var(--accent); margin:0 auto 2px}
  .body{width:14px; height:20px; background:var(--ok); border-radius:3px; margin:0 auto}
  .enter.anim{ animation: enter 2s ease-in-out forwards; }
  @keyframes enter { 0% { transform: translateX(0); opacity:1; } 80% { transform: translateX(150px); opacity:1; } 100% { transform: translateX(170px); opacity:.0; } }

  .btn{margin-top:16px; padding:10px 14px; background:var(--ok); color:#f4f4f4; border-radius:10px; text-decoration:none; font-weight:800}
</style>
<div class="wrap">
  <div class="top">
    <div class="pill ok">✅ Presença registrada</div>
    <h1>Matrícula <span class="mono">{{ code }}</span> confirmada!</h1>
    <div class="sub">Aproveite o evento <b>MultiplicadorAdemicon</b>!</div>
  </div>

  <div class="grid">
    <!-- Avião -->
    <div class="card">
      <h2>Aviação 🌍</h2>
      <div class="globe">
        <svg viewBox="0 0 200 200" width="180" height="180"><circle cx="100" cy="100" r="70" class="earth"/><path d="M110,70 C130,90 130,110 110,130 C90,110 70,110 70,100 C70,90 90,70 110,70Z" class="earth-land"/></svg>
        <div class="plane orbit anim"><svg viewBox="0 0 64 64" width="28" height="28"><path d="M4 30 L40 30 L60 20 L62 24 L46 32 L60 40 L58 44 L40 34 L4 34 Z" fill="#f4f4f4"/></svg></div>
      </div>
      <div class="hint">Passe o mouse: o avião orbita</div>
    </div>

    <!-- Formatura -->
    <div class="card">
      <h2>Festa de formatura 🎓</h2>
      <div class="grad">
        <div class="cap"></div>
        <div class="confetti">
          <span class="anim"></span><span class="anim"></span><span class="anim"></span><span class="anim"></span><span class="anim"></span>
          <span class="anim"></span><span class="anim"></span><span class="anim"></span><span class="anim"></span><span class="anim"></span>
        </div>
      </div>
      <div class="hint">Passe o mouse: confetes 🎉</div>
    </div>

    <!-- Carro -->
    <div class="card">
      <h2>Carro na estrada 🚗</h2>
      <div class="road">
        <div class="lane anim" style="animation: dash 1s linear infinite;"></div>
        <style>@keyframes dash { from{ transform: translateX(0); } to{ transform: translateX(-52px);} }</style>
        <div class="car drive anim">
          <div class="win"></div>
          <div class="wheel w1 spin anim"></div>
          <div class="wheel w2 spin anim"></div>
        </div>
      </div>
      <div class="hint">Passe o mouse: o carro anda</div>
    </div>

    <!-- Pessoa -->
    <div class="card">
      <h2>Chegando em casa 🏠</h2>
      <div class="yard">
        <div class="roof"></div>
        <div class="house"></div>
        <div class="door"></div>
        <div class="person enter anim">
          <div class="head"></div>
          <div class="body"></div>
        </div>
      </div>
      <div class="hint">Passe o mouse: pessoa entra</div>
    </div>
  </div>

  <a class="btn" href="/presenca">Registrar outra presença</a>
</div>
</html>
"""

def _success_page_presenca(code):
    cfg = current_app.config
    return render_template_string(
        PAGE_SUCCESS_PRESENCA,
        code=code,
        brand_bg=cfg.get("BRAND_BG", "#231f20"),
        brand_card=cfg.get("BRAND_CARD", "#2e2b2c"),
        brand_line=cfg.get("BRAND_LINE", "#3a3536"),
        brand_primary=cfg.get("BRAND_PRIMARY", "#7a1315"),
        brand_accent=cfg.get("BRAND_ACCENT", "#d1a34a"),
        logo_url=cfg.get("LOGO_URL", "")
    )

# ------------------ Rota principal ------------------
@presenca_bp.route("/", methods=["GET"])
@presenca_bp.route("/", methods=["GET"])
def presenca_page():
    code = (request.args.get("matricula") or "").strip().upper()
    tried = bool(code)
    today = dt.datetime.utcnow().date()

    if not tried:
        return render_template_string(PAGE, tried=False)

    # valida formato
    if not FORMAT.fullmatch(code):
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684)",
                                      matricula=code)

    # busca matrícula
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Matrícula não encontrada.",
                                      matricula=code)

    # ⚠️ defensivo
    status = getattr(m, "status", "active")

    if status != "active":
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg=f"Matrícula inativa (status: {status})",
                                      matricula=code)

    # verifica presença já existente
    existing = Presenca.query.filter_by(matricula_id=m.id, date_key=today).first()
    if existing:
        # ✅ usa a página de sucesso animada
        return _success_page_presenca(m.code)

    # registra nova presença
    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=dt.datetime.utcnow(),
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=(request.user_agent.string or "")[:300],
        source="link"
    )
    db.session.add(p)
    db.session.commit()

    # ✅ usa a página de sucesso animada
    return _success_page_presenca(m.code)

# ------------------ API JSON ------------------
@presenca_bp.get("/api")
def presenca_api():
    code = (request.args.get("matricula") or "").strip().upper()
    if not FORMAT.fullmatch(code):
        return jsonify({"ok": False, "msg": "Formato inválido"}), 400
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify({"ok": False, "msg": "Matrícula não encontrada"}), 404
    today = dt.datetime.utcnow().date()
    existing = Presenca.query.filter_by(matricula_id=m.id, date_key=today).first()
    if existing:
        return jsonify({"ok": True, "already": True, "code": m.code}), 200
    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=dt.datetime.utcnow(),
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=(request.user_agent.string or "")[:300],
        source="api"
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "already": False, "code": m.code}), 200

# ------------------ EXPORTS (CSV e JSON) ------------------
def _parse_date(s):
    try:
        return None if not s else __import__("datetime").date.fromisoformat(s)
    except Exception:
        return None

@presenca_bp.get("/export.csv")
def export_presencas_csv():
    """
    Exporta presenças em CSV.
    Filtros opcionais:
      ?start=YYYY-MM-DD&end=YYYY-MM-DD&code=MR25684
    """
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))
    code  = (request.args.get("code") or "").strip().upper()

    q = db.session.query(
        Presenca.id,
        Presenca.date_key,
        Presenca.timestamp,
        Matricula.code,
        Matricula.holder_name,
        Matricula.cpf,
        Matricula.status,
        Presenca.ip,
        Presenca.source
    ).join(Matricula, Matricula.id == Presenca.matricula_id)

    if start:
        q = q.filter(Presenca.date_key >= start)
    if end:
        q = q.filter(Presenca.date_key <= end)
    if code:
        q = q.filter(Matricula.code == code)

    q = q.order_by(Presenca.date_key.desc(), Presenca.timestamp.desc())
    rows = q.all()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id","date_key","timestamp_utc","code","holder_name","cpf","status","ip","source"])
    for r in rows:
        writer.writerow([
            r.id, r.date_key, r.timestamp.isoformat(),
            r.code, r.holder_name or "", r.cpf or "",
            r.status, r.ip or "", r.source or ""
        ])

    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=presencas.csv"
    return resp


@presenca_bp.get("/export.json")
def export_presencas_json():
    """
    Exporta presenças em JSON, com os mesmos filtros.
    """
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))
    code  = (request.args.get("code") or "").strip().upper()

    q = db.session.query(
        Presenca.id,
        Presenca.date_key,
        Presenca.timestamp,
        Matricula.code,
        Matricula.holder_name,
        Matricula.cpf,
        Matricula.status,
        Presenca.ip,
        Presenca.source
    ).join(Matricula, Matricula.id == Presenca.matricula_id)

    if start:
        q = q.filter(Presenca.date_key >= start)
    if end:
        q = q.filter(Presenca.date_key <= end)
    if code:
        q = q.filter(Matricula.code == code)

    q = q.order_by(Presenca.date_key.desc(), Presenca.timestamp.desc())
    rows = q.all()

    data = [{
        "id": r.id,
        "date_key": r.date_key.isoformat(),
        "timestamp_utc": r.timestamp.isoformat(),
        "code": r.code,
        "holder_name": r.holder_name,
        "cpf": r.cpf,
        "status": r.status,
        "ip": r.ip,
        "source": r.source
    } for r in rows]

    return jsonify({"count": len(data), "items": data})