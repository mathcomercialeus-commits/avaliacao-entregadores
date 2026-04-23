import os
import uuid
import hashlib
from datetime import datetime
from functools import wraps
from html import escape
from flask import Flask, request, redirect, url_for, session, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "TROQUE-ESSA-CHAVE-POR-UMA-SECRETA")

# URL do PostgreSQL (Render → DATABASE_URL)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurada. Defina a variável de ambiente DATABASE_URL.")

# Engine global do SQLAlchemy
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Flag global pra garantir que init_db rode só uma vez por processo
db_initialized = False

# ---------------- ANTIFRAUDE (CONFIG) ----------------
RATE_LIMIT_MINUTES = 3           # anti-spam (IP e device) em janela curta
WEEKLY_BLOCK_DAYS = 7            # 1 avaliação por semana por DISPOSITIVO
DRIVER_DEVICE_BLOCK_DAYS = 30    # 1 avaliação por motorista por dispositivo em 30 dias

# Se estiver em HTTPS e quiser forçar cookie Secure, setar env COOKIE_SECURE=1 no Render
COOKIE_SECURE_ENV = os.getenv("COOKIE_SECURE", "0") == "1"


# ---------------- BANCO DE DADOS (POSTGRES) ----------------

def init_db():
    """Cria as tabelas e o admin padrão, se ainda não existir (PostgreSQL)."""
    with engine.begin() as conn:
        # Tabela de usuários (admin e motoristas)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,       -- 'admin', 'cashier' ou 'driver'
                password_hash TEXT NOT NULL
            );
        """))

        # Tabela de avaliações
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ratings (
                id SERIAL PRIMARY KEY,
                driver_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                ip TEXT,
                fingerprint TEXT,
                device_id TEXT,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (driver_id) REFERENCES users(id)
            );
        """))

        # Garantir colunas (seguro em PostgreSQL)
        conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS comment TEXT;"))
        conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS fingerprint TEXT;"))
        conn.execute(text("ALTER TABLE ratings ADD COLUMN IF NOT EXISTS device_id TEXT;"))

        # Índices (performance + antifraude)
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ratings_ip_created ON ratings (ip, created_at);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ratings_fp_created ON ratings (fingerprint, created_at);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ratings_device_created ON ratings (device_id, created_at);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ratings_driver_device_created ON ratings (driver_id, device_id, created_at);"))

        # Cria admin padrão FARMALIMA se não existir
        cur = conn.execute(
            text("SELECT id FROM users WHERE username = :user AND role = 'admin'"),
            {"user": "FARMALIMA"},
        )
        if cur.fetchone() is None:
            password_hash = generate_password_hash("Farma@lima3535")
            conn.execute(
                text(
                    "INSERT INTO users (username, name, role, password_hash) "
                    "VALUES (:username, :name, 'admin', :password_hash)"
                ),
                {
                    "username": "FARMALIMA",
                    "name": "Administrador",
                    "password_hash": password_hash,
                },
            )


# ---------------- LAYOUT (MOBILE + TURQUESA + CARTÃO) ----------------

def render_page(title: str, body_html: str) -> str:
    """Monta uma página HTML responsiva com layout moderno."""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>{title} · Avaliação de Entregas</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        :root {{
            --turq-light: #4de1ff;
            --turq-main: #00bcd4;
            --turq-dark: #008ba3;
            --card-bg: #ffffff;
            --text-main: #023047;
            --text-muted: #6c757d;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            background: linear-gradient(145deg, #e0f7fa, #00bcd4);
            min-height: 100vh;
            color: var(--text-main);
        }}

        /* Cabeçalho fixo */
        .topbar {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 56px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0 16px;
            background: linear-gradient(135deg, var(--turq-main), var(--turq-dark));
            color: #fff;
            font-weight: 600;
            letter-spacing: 0.03em;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            z-index: 1000;
        }}

        .topbar span.logo-emoji {{
            margin-right: 8px;
            font-size: 22px;
        }}

        .topbar span.brand {{
            font-size: 16px;
            text-transform: uppercase;
        }}

        /* Área de conteúdo */
        .page {{
            min-height: 100vh;
            padding: 80px 12px 24px;
            display: flex;
            justify-content: center;
        }}

        /* Cartão central flutuando */
        .card {{
            width: 100%;
            max-width: 900px;
            background: var(--card-bg);
            border-radius: 18px;
            padding: 24px 18px 26px;
            box-shadow: 0 16px 45px rgba(0,0,0,0.18);
            position: relative;
            overflow: hidden;
        }}

        @media (min-width: 768px) {{
            .card {{
                padding: 32px 32px 34px;
                border-radius: 22px;
            }}
        }}

        .card::before {{
            content: "";
            position: absolute;
            inset: 0;
            background: radial-gradient(circle at 0 0, rgba(77,225,255,0.18), transparent 55%),
                        radial-gradient(circle at 100% 100%, rgba(0,188,212,0.10), transparent 55%);
            pointer-events: none;
        }}

        .card-inner {{
            position: relative;
            z-index: 1;
        }}

        h1, h2, h3 {{
            text-align: center;
            margin-top: 0;
        }}

        h1 {{
            font-size: 1.6rem;
            margin-bottom: 0.4rem;
        }}

        h3 {{
            font-size: 1.1rem;
            margin-bottom: 0.6rem;
            color: var(--text-muted);
        }}

        p {{
            margin: 0.4rem 0;
        }}

        .subtitle-center {{
            text-align: center;
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-bottom: 1.2rem;
        }}

        /* Formulários */
        form {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            width: 100%;
        }}

        label {{
            font-size: 0.9rem;
            font-weight: 500;
            color: var(--text-muted);
        }}

        input[type=text],
        input[type=password],
        select,
        textarea {{
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid #d0d7de;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
            font-family: inherit;
            background:#fff;
        }}

        textarea {{
            resize: vertical;
            min-height: 70px;
        }}

        input[type=text]:focus,
        input[type=password]:focus,
        select:focus,
        textarea:focus {{
            border-color: var(--turq-main);
            box-shadow: 0 0 0 2px rgba(0, 188, 212, 0.25);
        }}

        /* Botões principais */
        button {{
            padding: 11px 14px;
            border-radius: 999px;
            border: none;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.95rem;
            background: linear-gradient(135deg, var(--turq-main), var(--turq-dark));
            color: #fff;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.16);
            transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
        }}

        button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 14px 30px rgba(0,0,0,0.22);
            filter: brightness(1.03);
        }}

        button:active {{
            transform: translateY(0);
            box-shadow: 0 8px 18px rgba(0,0,0,0.18);
        }}

        .btn-outline {{
            background: #ffffff;
            color: var(--turq-dark);
            border: 1px solid rgba(0,188,212,0.25);
            box-shadow: none;
        }}

        .btn-danger {{
            background: linear-gradient(135deg, #ff5252, #e53935);
        }}

        .btn-warning {{
            background: linear-gradient(135deg, #ffb300, #ff8f00);
        }}

        .btn-full {{
            width: 100%;
        }}

        .btn-sm {{
            padding: 7px 12px;
            font-size: 0.8rem;
            box-shadow: none;
        }}

        /* Mensagens */
        .msg {{
            padding:10px 12px;
            background:#e3f2fd;
            border:1px solid #90caf9;
            border-radius:10px;
            margin-bottom:10px;
            font-size:0.9rem;
        }}

        .erro {{
            padding:10px 12px;
            background:#ffebee;
            border:1px solid #ef9a9a;
            border-radius:10px;
            margin-bottom:10px;
            font-size:0.9rem;
        }}

        /* Tabela elegante */
        .table-wrapper {{
            width: 100%;
            overflow-x: auto;
            margin-top: 14px;
        }}

        table {{
            width:100%;
            border-collapse:separate;
            border-spacing:0 6px;
            font-size:0.85rem;
        }}

        thead tr th {{
            background: rgba(2,48,71,0.06);
            padding:8px 10px;
            text-align:left;
            color:var(--text-muted);
            font-weight:600;
        }}

        tbody tr {{
            background:#ffffff;
            box-shadow:0 2px 8px rgba(0,0,0,0.04);
        }}

        tbody tr td {{
            padding:8px 10px;
            border-top:1px solid #f0f0f0;
            border-bottom:1px solid #f0f0f0;
        }}

        tbody tr td:first-child {{
            border-left:1px solid #f0f0f0;
            border-top-left-radius:12px;
            border-bottom-left-radius:12px;
        }}

        tbody tr td:last-child {{
            border-right:1px solid #f0f0f0;
            border-top-right-radius:12px;
            border-bottom-right-radius:12px;
        }}

        code {{
            font-size:0.75rem;
            background:#f1f8ff;
            padding:4px 6px;
            border-radius:6px;
            display:inline-block;
            max-width: 230px;
            overflow-wrap: break-word;
        }}

        .table-actions {{
            display:flex;
            flex-wrap:wrap;
            gap:6px;
        }}

        .section {{
            margin-bottom: 1.3rem;
        }}

        .section-title {{
            font-size:1.0rem;
            font-weight:600;
            margin-bottom:0.2rem;
        }}

        .section-subtitle {{
            font-size:0.85rem;
            color:var(--text-muted);
            margin-bottom:0.8rem;
        }}

        .spacer {{
            height: 12px;
        }}

        /* Estrelas estilo iFood (cliente) */
        .rating-container {{
            display:flex;
            flex-direction:column;
            gap:6px;
            margin:10px 0 6px;
        }}

        .rating-label {{
            font-size:0.9rem;
            color:var(--text-muted);
        }}

        .stars {{
            display:flex;
            flex-direction:row-reverse;
            justify-content:center;
            gap:4px;
        }}

        .stars input {{
            display:none;
        }}

        .stars label {{
            font-size:32px;
            cursor:pointer;
            color:#cfd8dc;
            transition:transform 0.12s ease, color 0.12s ease;
        }}

        .stars label:hover,
        .stars label:hover ~ label {{
            color:#ffd54f;
            transform:translateY(-1px);
        }}

        .stars input:checked ~ label {{
            color:#ffc107;
        }}

        .stars input#score-1:checked ~ label[for="score-1"] {{
            color:#ff6f00;
        }}

        .rating-text {{
            font-size:0.85rem;
            color:var(--text-muted);
            min-height:18px;
        }}

        /* Resumo Google-like no painel admin */
        .rating-summary {{
            font-size:0.8rem;
            color:var(--text-muted);
        }}

        .rating-main {{
            display:flex;
            align-items:baseline;
            gap:6px;
            margin-bottom:6px;
        }}

        .rating-value {{
            font-size:1.4rem;
            font-weight:700;
            color:var(--text-main);
        }}

        .rating-stars {{
            color:#ffc107;
            font-size:1rem;
        }}

        .rating-count {{
            color:var(--text-muted);
            font-size:0.8rem;
        }}

        .rating-bars {{
            display:flex;
            flex-direction:column;
            gap:3px;
        }}

        .rating-row {{
            display:flex;
            align-items:center;
            gap:6px;
        }}

        .rating-label-small {{
            width:22px;
            font-size:0.75rem;
            color:var(--text-muted);
        }}

        .rating-bar-outer {{
            flex:1;
            height:6px;
            border-radius:4px;
            background:#e0e0e0;
            overflow:hidden;
        }}

        .rating-bar-inner {{
            height:100%;
            border-radius:4px;
            background:linear-gradient(90deg, #00bcd4, #008ba3);
        }}

        .rating-qtd {{
            width:24px;
            text-align:right;
            font-size:0.75rem;
            color:var(--text-muted);
        }}

        /* Lista de comentários */
        .comment-list {{
            margin-top: 8px;
            max-height: 260px;
            overflow-y: auto;
            border-radius: 10px;
            border: 1px solid #e0e0e0;
            background: #fafafa;
            padding: 8px 10px;
        }}

        .comment-item {{
            border-bottom: 1px solid #e5e5e5;
            padding: 6px 0;
            font-size: 0.8rem;
        }}

        .comment-item:last-child {{
            border-bottom: none;
        }}

        .comment-header {{
            display:flex;
            justify-content:space-between;
            gap:6px;
            align-items:center;
            margin-bottom:2px;
        }}

        .comment-driver {{
            font-weight:600;
        }}

        .comment-stars {{
            color:#ffc107;
            font-size:0.8rem;
        }}

        .comment-date {{
            color:#9e9e9e;
            font-size:0.75rem;
        }}

        .comment-text {{
            color:#424242;
            white-space:pre-wrap;
        }}

        .stats-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
            gap:12px;
            margin-bottom:16px;
        }}

        .stat-card {{
            padding:14px 16px;
            border-radius:14px;
            background:linear-gradient(180deg, rgba(77,225,255,0.18), rgba(255,255,255,0.96));
            border:1px solid rgba(0,188,212,0.18);
        }}

        .stat-label {{
            color:var(--text-muted);
            font-size:0.82rem;
            margin-bottom:4px;
        }}

        .stat-value {{
            font-size:1.55rem;
            font-weight:700;
            color:var(--text-main);
        }}

        @media (max-width: 480px) {{
            h1 {{
                font-size:1.3rem;
            }}
            .topbar {{
                height:52px;
            }}
            .page {{
                padding-top:76px;
            }}
        }}
    </style>

    <script>
        function setupRatingText() {{
            var radios = document.querySelectorAll('input[name="score"]');
            var label = document.getElementById('rating-text');

            if (!radios || !label) return;

            var textos = {{
                1: "Muito ruim",
                2: "Ruim",
                3: "Ok",
                4: "Muito bom",
                5: "Excelente!"
            }};

            radios.forEach(function(r) {{
                r.addEventListener('change', function() {{
                    var v = parseInt(this.value);
                    label.textContent = textos[v] || "";
                }});
            }});
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            setupRatingText();
        }});
    </script>
</head>
<body>
    <header class="topbar">
        <span class="logo-emoji">🚚📦</span>
        <span class="brand">Avaliação de Entregas</span>
    </header>
    <main class="page">
        <div class="card">
            <div class="card-inner">
                {body_html}
            </div>
        </div>
    </main>
</body>
</html>
"""


# ---------------- AUXILIARES ----------------

def get_client_ip():
    """Tenta pegar o IP real do cliente, considerando proxy do Render."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


def device_fingerprint():
    """
    Fingerprint leve (ajuda junto com IP e device_id).
    Não é o principal, porque headers podem variar em celulares.
    """
    raw = "|".join([
        request.headers.get("User-Agent", "")[:300],
        request.headers.get("Accept-Language", "")[:200],
        request.headers.get("Sec-CH-UA", "")[:300],
        request.headers.get("Sec-CH-UA-Platform", "")[:100],
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_device_cookie(resp):
    """
    Garante cookie de device_id (did). Isso é o que resolve trocar de Wi-Fi/4G/VPN.
    """
    did = request.cookies.get("did")
    if not did:
        did = str(uuid.uuid4())

        secure_flag = COOKIE_SECURE_ENV or request.is_secure
        resp.set_cookie(
            "did",
            did,
            max_age=60 * 60 * 24 * 180,  # 180 dias
            httponly=True,
            samesite="Lax",
            secure=secure_flag,
        )
    return did


def current_user():
    if "user_id" in session:
        with engine.connect() as conn:
            cur = conn.execute(
                text("SELECT id, username, name, role, password_hash FROM users WHERE id = :id"),
                {"id": session["user_id"]},
            )
            user = cur.mappings().first()
            return user
    return None


def format_rating_date(value):
    if value is None:
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M")
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(value)


def esc(value):
    return escape(str(value), quote=True)


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("index"))
            if role and user["role"] != role:
                return "Acesso negado", 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------- GARANTIR QUE O BANCO EXISTA ----------------

@app.before_request
def ensure_db():
    global db_initialized
    if not db_initialized:
        init_db()
        db_initialized = True


# ---------------- ROTAS ----------------

@app.route("/")
def index():
    user = current_user()
    if user:
        role_map = {
            "admin": "Administrador",
            "cashier": "Caixa",
            "driver": "Motorista",
        }
        role_texto = role_map.get(user["role"], "Usuario")
        botoes = ""
        if user["role"] == "admin":
            botoes += '<p><button class="btn-full" onclick="window.location.href=\'/admin/dashboard\'">Painel do Administrador</button></p>'
        elif user["role"] == "cashier":
            botoes += '<p><button class="btn-full" onclick="window.location.href=\'/cashier/dashboard\'">Painel da Caixa</button></p>'
        else:
            botoes += '<p><button class="btn-full" onclick="window.location.href=\'/driver/painel\'">Painel do Motorista</button></p>'
        botoes += '<p><button class="btn-full btn-outline" onclick="window.location.href=\'/logout\'">Sair</button></p>'

        body = f"""
        <h1>Bem-vindo 👋</h1>
        <p class="subtitle-center">Controle profissional de avaliação de entregas, em tempo real.</p>
        <p style="text-align:center; margin-bottom:1.2rem;">
            Logado como: <strong>{user['name']} ({role_texto})</strong>
        </p>
        {botoes}
        """
    else:
        body = """
        <h1>🚚 Avaliação de Entregas</h1>
        <p class="subtitle-center">
            Motoristas mostram o QR Code.<br>
            Clientes avaliam o atendimento e o tempo de entrega em poucos toques.
        </p>
        <div class="section">
            <button class="btn-full" onclick="window.location.href='/admin/login'">Sou Administrador</button>
        </div>
        <div class="section">
            <button class="btn-full btn-outline" onclick="window.location.href='/cashier/login'">Sou Caixa</button>
        </div>
        <div class="section">
            <button class="btn-full btn-outline" onclick="window.location.href='/driver/login'">Sou Motorista</button>
        </div>
        """

    resp = make_response(render_page("Início", body))
    ensure_device_cookie(resp)
    return resp


# ----- LOGIN ADMIN -----

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    msg = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with engine.connect() as conn:
            cur = conn.execute(
                text("SELECT id, username, name, role, password_hash FROM users WHERE username = :u AND role = 'admin'"),
                {"u": username},
            )
            user = cur.mappings().first()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("admin_dashboard"))
        msg = "Usuário ou senha inválidos."

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Login do Administrador 🔐</h1>
    <p class="subtitle-center">Acesse para gerenciar motoristas e acompanhar as avaliações.</p>
    {msg_html}
    <form method="post" class="section">
        <label>Usuário</label>
        <input type="text" name="username" value="FARMALIMA">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """

    resp = make_response(render_page("Login Admin", body))
    ensure_device_cookie(resp)
    return resp


# ----- PAINEL ADMIN (GOOGLE-LIKE) -----

@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    flash_msg = request.args.get("msg", "").strip()
    flash_error = request.args.get("error", "").strip()
    with engine.connect() as conn:
        drivers = conn.execute(text("""
            SELECT
                u.id,
                u.name,
                COUNT(r.id) AS total_avaliacoes,
                COALESCE(ROUND(AVG(r.score)::numeric, 2), 0) AS media,
                SUM(CASE WHEN r.score = 5 THEN 1 ELSE 0 END) AS s5,
                SUM(CASE WHEN r.score = 4 THEN 1 ELSE 0 END) AS s4,
                SUM(CASE WHEN r.score = 3 THEN 1 ELSE 0 END) AS s3,
                SUM(CASE WHEN r.score = 2 THEN 1 ELSE 0 END) AS s2,
                SUM(CASE WHEN r.score = 1 THEN 1 ELSE 0 END) AS s1
            FROM users u
            LEFT JOIN ratings r ON u.id = r.driver_id
            WHERE u.role = 'driver'
            GROUP BY u.id, u.name
            ORDER BY u.name;
        """)).mappings().all()

        comments = conn.execute(text("""
            SELECT r.id, r.score, r.comment, r.created_at, u.name AS driver_name
            FROM ratings r
            JOIN users u ON u.id = r.driver_id
            WHERE TRIM(COALESCE(r.comment, '')) != ''
            ORDER BY r.created_at DESC
            LIMIT 30;
        """)).mappings().all()

    base_url = request.url_root.rstrip("/")

    linhas = ""
    for d in drivers:
        total = d["total_avaliacoes"] or 0
        media = d["media"] or 0
        s5 = d["s5"] or 0
        s4 = d["s4"] or 0
        s3 = d["s3"] or 0
        s2 = d["s2"] or 0
        s1 = d["s1"] or 0

        def perc(c):
            return int(round(c * 100 / total)) if total > 0 else 0

        p5 = perc(s5)
        p4 = perc(s4)
        p3 = perc(s3)
        p2 = perc(s2)
        p1 = perc(s1)

        link_avaliacao = f"{base_url}{url_for('rate_driver', driver_id=d['id'])}"

        rating_html = f"""
        <div class="rating-summary">
            <div class="rating-main">
                <span class="rating-value">{media}</span>
                <span class="rating-stars">★★★★★</span>
                <span class="rating-count">({total} avaliações)</span>
            </div>
            <div class="rating-bars">
                <div class="rating-row">
                    <span class="rating-label-small">5★</span>
                    <div class="rating-bar-outer">
                        <div class="rating-bar-inner" style="width: {p5}%;"></div>
                    </div>
                    <span class="rating-qtd">{s5}</span>
                </div>
                <div class="rating-row">
                    <span class="rating-label-small">4★</span>
                    <div class="rating-bar-outer">
                        <div class="rating-bar-inner" style="width: {p4}%;"></div>
                    </div>
                    <span class="rating-qtd">{s4}</span>
                </div>
                <div class="rating-row">
                    <span class="rating-label-small">3★</span>
                    <div class="rating-bar-outer">
                        <div class="rating-bar-inner" style="width: {p3}%;"></div>
                    </div>
                    <span class="rating-qtd">{s3}</span>
                </div>
                <div class="rating-row">
                    <span class="rating-label-small">2★</span>
                    <div class="rating-bar-outer">
                        <div class="rating-bar-inner" style="width: {p2}%;"></div>
                    </div>
                    <span class="rating-qtd">{s2}</span>
                </div>
                <div class="rating-row">
                    <span class="rating-label-small">1★</span>
                    <div class="rating-bar-outer">
                        <div class="rating-bar-inner" style="width: {p1}%;"></div>
                    </div>
                    <span class="rating-qtd">{s1}</span>
                </div>
            </div>
        </div>
        """

        linhas += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{rating_html}</td>
            <td><code>{link_avaliacao}</code></td>
            <td>
                <div class="table-actions">
                    <form method="post" action="/admin/reset_ratings/{d['id']}">
                        <button class="btn-warning btn-sm"
                            onclick="return confirm('Zerar as avaliações deste motorista?');">
                            Zerar
                        </button>
                    </form>
                    <form method="post" action="/admin/delete_driver/{d['id']}">
                        <button class="btn-danger btn-sm"
                            onclick="return confirm('EXCLUIR este motorista e todas as avaliações dele?');">
                            Excluir
                        </button>
                    </form>
                </div>
            </td>
        </tr>
        """

    comments_html = ""
    if comments:
        for c in comments:
            stars = "★" * c["score"] + "☆" * (5 - c["score"])
            comment_text = (c["comment"] or "").strip()
            comments_html += f"""
            <div class="comment-item">
                <div class="comment-header">
                    <span class="comment-driver">{c['driver_name']}</span>
                    <span class="comment-stars">{stars}</span>
                </div>
                <div class="comment-date">{c['created_at']}</div>
                <div class="comment-text">{comment_text}</div>
            </div>
            """

    body = f"""
    <h1>Painel do Administrador 🧑‍💼</h1>
    <p class="subtitle-center">
        Veja notas, acompanhe comentários e gerencie motoristas.
    </p>
    {'<div class="msg">' + esc(flash_msg) + '</div>' if flash_msg else ''}
    {'<div class="erro">' + esc(flash_error) + '</div>' if flash_error else ''}

    <div class="section">
        <div class="section-title">Equipe da caixa</div>
        <div class="section-subtitle">Cadastre e gerencie os acessos da caixa em uma Ã¡rea separada.</div>
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/cashiers'">Gerenciar caixas</button>
    </div>

    <div class="section">
        <div class="section-title">Cadastrar novo motorista</div>
        <div class="section-subtitle">Crie o login que o entregador vai usar para gerar o QR Code.</div>
        <form method="post" action="/admin/create_driver">
            <label>Nome do motorista</label>
            <input type="text" name="name" required>
            <label>Usuário para login do motorista</label>
            <input type="text" name="username" required>
            <label>Senha para login do motorista</label>
            <input type="password" name="password" required>
            <button type="submit">Cadastrar Motorista</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Motoristas cadastrados</div>
        <div class="section-subtitle">
            Cada linha mostra a média, total de avaliações e barras 5★ a 1★, igual ao Google Reviews.
        </div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Motorista</th>
                        <th>Avaliações</th>
                        <th>Link público</th>
                        <th>Ações</th>
                    </tr>
                </thead>
                <tbody>
                    {linhas}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Comentários recentes dos clientes</div>
        <div class="section-subtitle">
            Últimos 30 comentários com nota e motorista correspondente.
        </div>
        <div class="comment-list">
            {comments_html if comments_html else "<div class='comment-text'>Ainda não há comentários registrados.</div>"}
        </div>
    </div>

    <div class="section">
        <form method="post" action="/admin/reset_all_ratings">
            <button class="btn-danger btn-full"
                onclick="return confirm('ZERAR TODAS as avaliações do sistema?');">
                Zerar TODAS as avaliações
            </button>
        </form>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar ao início</button>
    </div>
    """

    resp = make_response(render_page("Painel Admin", body))
    ensure_device_cookie(resp)
    return resp


@app.route("/admin/create_driver", methods=["POST"])
@login_required(role="admin")
def create_driver():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not name or not username or not password:
        return "Dados inválidos", 400

    try:
        password_hash = generate_password_hash(password)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, name, role, password_hash) "
                    "VALUES (:username, :name, 'driver', :password_hash)"
                ),
                {"username": username, "name": name, "password_hash": password_hash},
            )
    except IntegrityError:
        return "Usuário já existe", 400

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/cashiers")
@login_required(role="admin")
def admin_cashiers():
    flash_msg = request.args.get("msg", "").strip()
    flash_error = request.args.get("error", "").strip()
    with engine.connect() as conn:
        cashiers = conn.execute(text("""
            SELECT id, name, username
            FROM users
            WHERE role = 'cashier'
            ORDER BY name;
        """)).mappings().all()

    rows = ""
    for cashier in cashiers:
        rows += f"""
        <tr>
            <td>{esc(cashier['name'])}</td>
            <td>{esc(cashier['username'])}</td>
            <td>
                <div class="table-actions">
                    <form method="post" action="/admin/delete_cashier/{cashier['id']}">
                        <button class="btn-danger btn-sm"
                            onclick="return confirm('Excluir este caixa?');">
                            Excluir
                        </button>
                    </form>
                </div>
            </td>
        </tr>
        """

    body = f"""
    <h1>Gestao de Caixas</h1>
    <p class="subtitle-center">O administrador cria os acessos da equipe do caixa.</p>
    {'<div class="msg">' + esc(flash_msg) + '</div>' if flash_msg else ''}
    {'<div class="erro">' + esc(flash_error) + '</div>' if flash_error else ''}

    <div class="section">
        <div class="section-title">Cadastrar novo caixa</div>
        <div class="section-subtitle">Esse acesso entra somente na tela de impressao.</div>
        <form method="post" action="/admin/create_cashier">
            <label>Nome do caixa</label>
            <input type="text" name="name" required>
            <label>Usuario para login do caixa</label>
            <input type="text" name="username" required>
            <label>Senha para login do caixa</label>
            <input type="password" name="password" required>
            <button type="submit">Cadastrar Caixa</button>
        </form>
    </div>

    <div class="section">
        <div class="section-title">Caixas cadastrados</div>
        <div class="section-subtitle">Esses usuarios acessam apenas a area da caixa para impressao.</div>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Nome</th>
                        <th>Usuario</th>
                        <th>Acoes</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/admin/dashboard'">Voltar para o admin</button>
    </div>
    """

    resp = make_response(render_page("Gestao de Caixas", body))
    ensure_device_cookie(resp)
    return resp


@app.route("/admin/create_cashier", methods=["POST"])
@login_required(role="admin")
def create_cashier():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not name or not username or not password:
        return "Dados invÃ¡lidos", 400

    try:
        password_hash = generate_password_hash(password)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, name, role, password_hash) "
                    "VALUES (:username, :name, 'cashier', :password_hash)"
                ),
                {"username": username, "name": name, "password_hash": password_hash},
            )
    except IntegrityError:
        return redirect(url_for("admin_cashiers", error="UsuÃ¡rio jÃ¡ existe"))

    return redirect(url_for("admin_cashiers", msg="Caixa cadastrado com sucesso"))


@app.route("/admin/delete_cashier/<int:cashier_id>", methods=["POST"])
@login_required(role="admin")
def delete_cashier(cashier_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id AND role = 'cashier'"), {"id": cashier_id})
    return redirect(url_for("admin_cashiers", msg="Caixa removido com sucesso"))


@app.route("/admin/reset_ratings/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def reset_ratings(driver_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ratings WHERE driver_id = :id"), {"id": driver_id})
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reset_all_ratings", methods=["POST"])
@login_required(role="admin")
def reset_all_ratings():
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ratings"))
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete_driver/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def delete_driver(driver_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ratings WHERE driver_id = :id"), {"id": driver_id})
        conn.execute(text("DELETE FROM users WHERE id = :id AND role = 'driver'"), {"id": driver_id})
    return redirect(url_for("admin_dashboard"))


# ----- LOGIN CAIXA -----

@app.route("/cashier/login", methods=["GET", "POST"])
def cashier_login():
    msg = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with engine.connect() as conn:
            cur = conn.execute(
                text("SELECT id, username, name, role, password_hash FROM users WHERE username = :u AND role = 'cashier'"),
                {"u": username},
            )
            user = cur.mappings().first()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("cashier_dashboard"))
        msg = "UsuÃ¡rio ou senha invÃ¡lidos."

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Login da Caixa</h1>
    <p class="subtitle-center">
        Entre para escolher o motorista cadastrado e imprimir a etiqueta de avaliaÃ§Ã£o.
    </p>
    {msg_html}
    <form method="post" class="section">
        <label>UsuÃ¡rio</label>
        <input type="text" name="username">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """

    resp = make_response(render_page("Login Caixa", body))
    ensure_device_cookie(resp)
    return resp


@app.route("/cashier/dashboard")
@login_required(role="cashier")
def cashier_dashboard():
    user = current_user()
    flash_msg = request.args.get("msg", "").strip()
    flash_error = request.args.get("error", "").strip()
    with engine.connect() as conn:
        drivers = conn.execute(text("""
            SELECT id, name, username
            FROM users
            WHERE role = 'driver'
            ORDER BY name;
        """)).mappings().all()

    options_html = "".join(
        f'<option value="{driver["id"]}">{esc(driver["name"])} ({esc(driver["username"])})</option>'
        for driver in drivers
    )

    body = f"""
    <h1>Painel da Caixa</h1>
    <p class="subtitle-center">
        Login ativo: <strong>{esc(user['name'])}</strong>. Escolha o motorista cadastrado e gere a etiqueta.
    </p>
    {'<div class="msg">' + esc(flash_msg) + '</div>' if flash_msg else ''}
    {'<div class="erro">' + esc(flash_error) + '</div>' if flash_error else ''}

    <div class="section">
        <div class="section-title">Imprimir etiqueta</div>
        <div class="section-subtitle">A caixa entra apenas para escolher o motorista e imprimir.</div>
        <form method="get" action="/cashier/print_label" target="_blank">
            <label>Motorista cadastrado</label>
            <select name="driver_id" required>
                <option value="">Selecione um motorista</option>
                {options_html}
            </select>
            <button type="submit" class="btn-full">Gerar etiqueta com QR Code</button>
        </form>
    </div>

    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """

    resp = make_response(render_page("Painel da Caixa", body))
    ensure_device_cookie(resp)
    return resp


@app.route("/cashier/print_label")
@login_required(role="cashier")
def print_label():
    try:
        driver_id = int(request.args.get("driver_id", "0"))
    except ValueError:
        driver_id = 0

    if driver_id <= 0:
        return redirect(url_for("cashier_dashboard", error="Selecione um motorista cadastrado para imprimir a etiqueta."))

    with engine.connect() as conn:
        driver = conn.execute(
            text("SELECT id, name, username FROM users WHERE id = :id AND role = 'driver'"),
            {"id": driver_id},
        ).mappings().first()

    if not driver:
        return redirect(url_for("cashier_dashboard", error="Motorista nÃ£o encontrado. Confira a lista cadastrada."))

    rate_url = request.url_root.rstrip("/") + url_for("rate_driver", driver_id=driver["id"])
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=320x320&data={rate_url}"

    body = f"""
    <style>
        .label-shell {{
            display:flex;
            flex-direction:column;
            align-items:center;
            gap:16px;
        }}

        .label-card {{
            width:100%;
            max-width:420px;
            padding:18px;
            text-align:center;
            border-radius:18px;
            background:#fff;
            border:2px dashed rgba(2,48,71,0.18);
            box-shadow:0 14px 34px rgba(0,0,0,0.10);
        }}

        .label-eyebrow {{
            margin-bottom:8px;
            color:#008ba3;
            font-size:0.78rem;
            font-weight:800;
            letter-spacing:0.08em;
            text-transform:uppercase;
        }}

        .label-title {{
            margin-bottom:8px;
            font-size:1.35rem;
            font-weight:800;
            line-height:1.2;
        }}

        .label-driver {{
            margin-bottom:14px;
            font-size:1rem;
        }}

        .label-qr img {{
            width:220px;
            height:220px;
            object-fit:contain;
        }}

        .label-footer {{
            margin-top:12px;
            color:#526674;
            font-size:0.92rem;
        }}

        .label-actions {{
            display:flex;
            gap:10px;
            width:100%;
            max-width:420px;
        }}

        .label-actions button {{
            flex:1;
        }}

        @media print {{
            .topbar,
            .label-actions {{
                display:none !important;
            }}

            .page {{
                padding:0 !important;
                min-height:auto !important;
            }}

            .card {{
                max-width:none !important;
                padding:0 !important;
                border-radius:0 !important;
                box-shadow:none !important;
            }}

            .label-card {{
                width:90mm;
                min-height:60mm;
                margin:0 auto;
                border:1px solid #d6dde2;
                box-shadow:none;
                page-break-inside:avoid;
            }}
        }}
    </style>

    <div class="label-shell">
        <div class="label-card">
            <div class="label-eyebrow">Etiqueta de avaliaÃ§Ã£o</div>
            <div class="label-title">Avalie nosso entregador e nossa entrega</div>
            <div class="label-driver">Motorista: <strong>{esc(driver['name'])}</strong></div>
            <div class="label-qr">
                <img src="{qr_url}" alt="QR Code para avaliar a entrega">
            </div>
            <div class="label-footer">
                Aponte a cÃ¢mera do celular para o QR Code e deixe sua nota.
            </div>
        </div>

        <div class="label-actions">
            <button type="button" onclick="window.print()">Imprimir novamente</button>
            <button type="button" class="btn-outline" onclick="window.close()">Fechar</button>
        </div>
    </div>

    <script>
        window.addEventListener("load", function() {{
            setTimeout(function() {{
                window.print();
            }}, 250);
        }});
    </script>
    """

    resp = make_response(render_page("Imprimir Etiqueta", body))
    ensure_device_cookie(resp)
    return resp


# ----- LOGIN MOTORISTA -----

@app.route("/driver/login", methods=["GET", "POST"])
def driver_login():
    msg = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with engine.connect() as conn:
            cur = conn.execute(
                text("SELECT id, username, name, role, password_hash FROM users WHERE username = :u AND role = 'driver'"),
                {"u": username},
            )
            user = cur.mappings().first()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("driver_panel"))
        msg = "Usuário ou senha inválidos."

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Login do Motorista 🛵</h1>
    <p class="subtitle-center">
        Entre para acompanhar apenas suas avaliaÃ§Ãµes e os comentÃ¡rios dos clientes.
    </p>
    {msg_html}
    <form method="post" class="section">
        <label>Usuário</label>
        <input type="text" name="username">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit" class="btn-full">Entrar</button>
    </form>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    """

    resp = make_response(render_page("Login Motorista", body))
    ensure_device_cookie(resp)
    return resp


# ----- PAINEL MOTORISTA (QR CODE) -----

@app.route("/driver/painel")
@login_required(role="driver")
def driver_panel():
    user = current_user()
    with engine.connect() as conn:
        summary = conn.execute(text("""
            SELECT
                COUNT(*) AS total_avaliacoes,
                COALESCE(ROUND(AVG(score)::numeric, 2), 0) AS media
            FROM ratings
            WHERE driver_id = :id
        """), {"id": user["id"]}).mappings().first()

        ratings = conn.execute(text("""
            SELECT score, comment, created_at
            FROM ratings
            WHERE driver_id = :id
            ORDER BY created_at DESC, id DESC
        """), {"id": user["id"]}).mappings().all()

    reviews_html = ""
    for rating in ratings:
        comment = (rating["comment"] or "").strip()
        comment_html = (
            f'<div class="comment-text">{esc(comment)}</div>'
            if comment
            else '<div class="comment-text" style="font-style:italic;color:#78909c;">Cliente nÃ£o deixou comentÃ¡rio nesta avaliaÃ§Ã£o.</div>'
        )
        reviews_html += f"""
        <div class="comment-item">
            <div class="comment-header">
                <span class="comment-driver">Nota: {rating['score']}/5</span>
                <span class="comment-date">{esc(format_rating_date(rating['created_at']))}</span>
            </div>
            {comment_html}
        </div>
        """

    if not reviews_html:
        reviews_html = "<div class='comment-text'>Ainda nÃ£o hÃ¡ avaliaÃ§Ãµes registradas para este motorista.</div>"

    body = f"""
    <h1>Painel do Motorista 🚚</h1>
    <p class="subtitle-center">
        Aqui vocÃª acompanha somente suas avaliaÃ§Ãµes e os comentÃ¡rios recebidos dos clientes.
    </p>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Sua mÃ©dia atual</div>
            <div class="stat-value">{summary['media']}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total de avaliaÃ§Ãµes</div>
            <div class="stat-value">{summary['total_avaliacoes']}</div>
        </div>
    </div>
    <div class="section">
        <div class="section-title">AvaliaÃ§Ãµes e comentÃ¡rios recebidos</div>
        <div class="section-subtitle">Cada item mostra a nota individual e o comentÃ¡rio deixado pelo cliente.</div>
        <div class="comment-list">
            {reviews_html}
        </div>
    </div>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """

    resp = make_response(render_page("Painel Motorista", body))
    ensure_device_cookie(resp)
    return resp


# ----- PÁGINA DE AVALIAÇÃO (COM COMENTÁRIO + ANTIFRAUDE) -----

@app.route("/avaliar/<int:driver_id>", methods=["GET", "POST"])
def rate_driver(driver_id):
    with engine.connect() as conn:
        cur = conn.execute(
            text("SELECT id, username, name, role FROM users WHERE id = :id AND role = 'driver'"),
            {"id": driver_id},
        )
        driver = cur.mappings().first()

    if not driver:
        return "Motorista não encontrado", 404

    ip = get_client_ip()
    fp = device_fingerprint()

    # did (device_id): se não vier cookie, gera um agora (e salva no response)
    did = request.cookies.get("did") or str(uuid.uuid4())

    msg = ""

    if request.method == "POST":
        # Honeypot anti-bot
        if request.form.get("website", "").strip():
            msg = "Avaliação inválida."
        else:
            # Rate limit curto: por IP e por device_id
            with engine.connect() as conn:
                r_ip = conn.execute(text(f"""
                    SELECT COUNT(*) AS total
                    FROM ratings
                    WHERE ip = :ip
                      AND created_at >= NOW() - INTERVAL '{RATE_LIMIT_MINUTES} minutes'
                """), {"ip": ip}).mappings().first()

                r_did = conn.execute(text(f"""
                    SELECT COUNT(*) AS total
                    FROM ratings
                    WHERE device_id = :did
                      AND created_at >= NOW() - INTERVAL '{RATE_LIMIT_MINUTES} minutes'
                """), {"did": did}).mappings().first()

            if (r_ip and (r_ip["total"] or 0) > 0) or (r_did and (r_did["total"] or 0) > 0):
                msg = "Muitas tentativas em pouco tempo. Aguarde alguns minutos e tente novamente."
            else:
                # Anti-inflar: por motorista + device_id
                with engine.connect() as conn:
                    r_driver = conn.execute(text(f"""
                        SELECT COUNT(*) AS total
                        FROM ratings
                        WHERE driver_id = :driver_id
                          AND device_id = :did
                          AND created_at >= NOW() - INTERVAL '{DRIVER_DEVICE_BLOCK_DAYS} days'
                    """), {"driver_id": driver_id, "did": did}).mappings().first()

                if r_driver and (r_driver["total"] or 0) > 0:
                    msg = "Você já avaliou este motorista recentemente neste dispositivo."
                else:
                    # BLOQUEIO PRINCIPAL: semanal por device_id (independe de Wi-Fi)
                    with engine.connect() as conn:
                        r_week = conn.execute(text(f"""
                            SELECT COUNT(*) AS total
                            FROM ratings
                            WHERE device_id = :did
                              AND created_at >= NOW() - INTERVAL '{WEEKLY_BLOCK_DAYS} days'
                        """), {"did": did}).mappings().first()

                    if r_week and (r_week["total"] or 0) > 0:
                        msg = "Você já fez uma avaliação recentemente. Só é permitido 1 avaliação por semana neste dispositivo."
                    else:
                        try:
                            score = int(request.form.get("score", "0"))
                        except ValueError:
                            score = 0

                        comment = request.form.get("comment", "").strip()

                        if score < 1 or score > 5:
                            msg = "Selecione uma nota entre 1 e 5 estrelas."
                        else:
                            with engine.begin() as conn:
                                conn.execute(
                                    text(
                                        "INSERT INTO ratings (driver_id, score, ip, fingerprint, device_id, comment) "
                                        "VALUES (:driver_id, :score, :ip, :fingerprint, :device_id, :comment)"
                                    ),
                                    {
                                        "driver_id": driver_id,
                                        "score": score,
                                        "ip": ip,
                                        "fingerprint": fp,
                                        "device_id": did,
                                        "comment": comment,
                                    },
                                )

                            body = f"""
                            <h1>Obrigado pela sua avaliação 💙</h1>
                            <p class="subtitle-center">
                                Sua opinião ajuda a melhorar a qualidade das entregas.
                            </p>
                            <p style="text-align:center; margin-top:1rem;">
                                Motorista avaliado: <strong>{driver['name']}</strong>
                            </p>
                            """
                            resp = make_response(render_page("Obrigado", body))
                            ensure_device_cookie(resp)
                            return resp

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Avalie sua entrega ⭐</h1>
    <p class="subtitle-center">
        Motorista: <strong>{driver['name']}</strong><br>
        Como você avalia <strong>atendimento</strong> e <strong>tempo de entrega</strong>?
    </p>
    {msg_html}
    <form method="post">
        <!-- Honeypot anti-bot (invisível) -->
        <input type="text" name="website" style="display:none" tabindex="-1" autocomplete="off">

        <div class="rating-container">
            <div class="rating-label">Toque nas estrelas para escolher a nota:</div>
            <div class="stars">
                <input type="radio" id="score-5" name="score" value="5">
                <label for="score-5">★</label>
                <input type="radio" id="score-4" name="score" value="4">
                <label for="score-4">★</label>
                <input type="radio" id="score-3" name="score" value="3">
                <label for="score-3">★</label>
                <input type="radio" id="score-2" name="score" value="2">
                <label for="score-2">★</label>
                <input type="radio" id="score-1" name="score" value="1">
                <label for="score-1">★</label>
            </div>
            <div id="rating-text" class="rating-text"></div>
        </div>

        <label for="comment">Comentário (opcional)</label>
        <textarea name="comment" id="comment" placeholder="Conte rapidamente como foi sua experiência com a entrega."></textarea>

        <div class="spacer"></div>
        <button type="submit" class="btn-full">Enviar avaliação</button>
    </form>
    """

    resp = make_response(render_page("Avaliar Entregador", body))
    ensure_device_cookie(resp)
    return resp


# ----- LOGOUT -----

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------- MAIN (LOCAL) ----------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
