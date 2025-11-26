import sqlite3
from flask import Flask, request, redirect, url_for, session, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "TROQUE-ESSA-CHAVE-POR-UMA-SECRETA"
DATABASE = "avaliacao_entregadores.db"

# Flag global pra garantir que init_db rode só uma vez por processo
db_initialized = False


# ---------------- BANCO DE DADOS ----------------

def init_db():
    """Cria as tabelas e o admin padrão, se ainda não existir."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    # Tabela de usuários (admin e motoristas)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,       -- 'admin' ou 'driver'
            password_hash TEXT NOT NULL
        );
    """)

    # Tabela de avaliações (já com IP e comentário)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            ip TEXT,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        );
    """)

    # Garantir que exista coluna comment em bancos antigos
    try:
        conn.execute("ALTER TABLE ratings ADD COLUMN comment TEXT;")
    except sqlite3.OperationalError:
        pass

    # Cria admin padrão FARMALIMA se não existir
    cur = conn.execute("SELECT id FROM users WHERE username = ?", ("FARMALIMA",))
    if cur.fetchone() is None:
        password_hash = generate_password_hash("Farma@lima3535")
        conn.execute(
            "INSERT INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)",
            ("FARMALIMA", "Administrador", "admin", password_hash),
        )

    conn.commit()
    conn.close()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


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
            padding: 80px 12px 24px; /* espaço pro cabeçalho */
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
        textarea {{
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid #d0d7de;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s ease;
            font-family: inherit;
        }}

        textarea {{
            resize: vertical;
            min-height: 70px;
        }}

        input[type=text]:focus,
        input[type=password]:focus,
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
            padding: 10px 12px;
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
        // Atualiza texto de "nota" igual apps de delivery
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


def current_user():
    if "user_id" in session:
        db = get_db()
        cur = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        return cur.fetchone()
    return None


def login_required(role=None):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("index"))
            if role and user["role"] != role:
                return "Acesso negado", 403
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
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
        role_texto = "Administrador" if user["role"] == "admin" else "Motorista"
        botoes = ""
        if user["role"] == "admin":
            botoes += '<p><button class="btn-full" onclick="window.location.href=\'/admin/dashboard\'">Painel do Administrador</button></p>'
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
            <button class="btn-full btn-outline" onclick="window.location.href='/driver/login'">Sou Motorista</button>
        </div>
        """
    return render_page("Início", body)


# ----- LOGIN ADMIN -----

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    msg = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        cur = db.execute("SELECT * FROM users WHERE username = ? AND role = 'admin'", (username,))
        user = cur.fetchone()
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
    return render_page("Login Admin", body)


# ----- PAINEL ADMIN (GOOGLE-LIKE) -----

@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    db = get_db()
    # Query com média, total e contagem por estrela
    cur = db.execute("""
        SELECT
            u.id,
            u.name,
            COUNT(r.id) AS total_avaliacoes,
            COALESCE(ROUND(AVG(r.score), 2), 0) AS media,
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
    """)
    drivers = cur.fetchall()

    # Últimos comentários
    cur_comments = db.execute("""
        SELECT r.id, r.score, r.comment, r.created_at, u.name AS driver_name
        FROM ratings r
        JOIN users u ON u.id = r.driver_id
        WHERE TRIM(COALESCE(r.comment, '')) != ''
        ORDER BY r.created_at DESC
        LIMIT 30;
    """)
    comments = cur_comments.fetchall()

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
        Veja notas no estilo Google, acompanhe comentários e gerencie motoristas.
    </p>

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
    return render_page("Painel Admin", body)


@app.route("/admin/create_driver", methods=["POST"])
@login_required(role="admin")
def create_driver():
    name = request.form.get("name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not name or not username or not password:
        return "Dados inválidos", 400

    db = get_db()
    try:
        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (username, name, role, password_hash) VALUES (?, ?, 'driver', ?)",
            (username, name, password_hash),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return "Usuário já existe", 400

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reset_ratings/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def reset_ratings(driver_id):
    db = get_db()
    db.execute("DELETE FROM ratings WHERE driver_id = ?", (driver_id,))
    db.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reset_all_ratings", methods=["POST"])
@login_required(role="admin")
def reset_all_ratings():
    db = get_db()
    db.execute("DELETE FROM ratings")
    db.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete_driver/<int:driver_id>", methods=["POST"])
@login_required(role="admin")
def delete_driver(driver_id):
    db = get_db()
    db.execute("DELETE FROM ratings WHERE driver_id = ?", (driver_id,))
    db.execute("DELETE FROM users WHERE id = ?", (driver_id,))
    db.commit()
    return redirect(url_for("admin_dashboard"))


# ----- LOGIN MOTORISTA -----

@app.route("/driver/login", methods=["GET", "POST"])
def driver_login():
    msg = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        cur = db.execute("SELECT * FROM users WHERE username = ? AND role = 'driver'", (username,))
        user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("driver_panel"))
        msg = "Usuário ou senha inválidos."

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Login do Motorista 🛵</h1>
    <p class="subtitle-center">
        Entre para gerar seu QR Code e compartilhar com os clientes.
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
    return render_page("Login Motorista", body)


# ----- PAINEL MOTORISTA (QR CODE) -----

@app.route("/driver/painel")
@login_required(role="driver")
def driver_panel():
    user = current_user()
    rate_url = request.url_root.rstrip('/') + url_for("rate_driver", driver_id=user["id"])
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={rate_url}"

    body = f"""
    <h1>Painel do Motorista 🚚</h1>
    <p class="subtitle-center">
        Mostre o QR Code abaixo para o cliente avaliar atendimento e tempo de entrega.
    </p>
    <div style="text-align:center; margin: 10px 0 6px;">
        <img src="{qr_url}" alt="QR Code" style="border-radius:16px; box-shadow:0 10px 30px rgba(0,0,0,0.20); max-width:80vw;">
    </div>
    <p class="subtitle-center" style="font-size:0.85rem;">
        Ou compartilhe o link direto:
    </p>
    <p style="text-align:center; margin-bottom:1rem;">
        <code>{rate_url}</code>
    </p>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/'">Voltar</button>
    </div>
    <div class="section">
        <button class="btn-full btn-outline" type="button" onclick="window.location.href='/logout'">Sair</button>
    </div>
    """
    return render_page("Painel Motorista", body)


# ----- PÁGINA DE AVALIAÇÃO (COM COMENTÁRIO + ANTIFRAUDE) -----

@app.route("/avaliar/<int:driver_id>", methods=["GET", "POST"])
def rate_driver(driver_id):
    db = get_db()
    cur = db.execute("SELECT * FROM users WHERE id = ? AND role = 'driver'", (driver_id,))
    driver = cur.fetchone()
    if not driver:
        return "Motorista não encontrado", 404

    msg = ""
    ip = get_client_ip()

    if request.method == "POST":
        # Verifica se este IP já avaliou ALGUM motorista nos últimos 7 dias
        cur = db.execute(
            "SELECT COUNT(*) AS total FROM ratings "
            "WHERE ip = ? AND created_at >= datetime('now','-7 days')",
            (ip,),
        )
        row = cur.fetchone()
        if row["total"] > 0:
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
                db.execute(
                    "INSERT INTO ratings (driver_id, score, ip, comment) VALUES (?, ?, ?, ?)",
                    (driver_id, score, ip, comment),
                )
                db.commit()
                body = f"""
                <h1>Obrigado pela sua avaliação 💙</h1>
                <p class="subtitle-center">
                    Sua opinião ajuda a melhorar a qualidade das entregas.
                </p>
                <p style="text-align:center; margin-top:1rem;">
                    Motorista avaliado: <strong>{driver['name']}</strong>
                </p>
                """
                return render_page("Obrigado", body)

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Avalie sua entrega ⭐</h1>
    <p class="subtitle-center">
        Motorista: <strong>{driver['name']}</strong><br>
        Como você avalia <strong>atendimento</strong> e <strong>tempo de entrega</strong>?
    </p>
    {msg_html}
    <form method="post">
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
    return render_page("Avaliar Entregador", body)


# ----- LOGOUT -----

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------- MAIN (LOCAL) ----------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
