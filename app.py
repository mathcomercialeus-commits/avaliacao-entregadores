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

    # Tabela de avaliações (já com IP)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (driver_id) REFERENCES users(id)
        );
    """)

    # Se o banco for antigo e não tiver coluna ip, tenta adicionar
    try:
        conn.execute("ALTER TABLE ratings ADD COLUMN ip TEXT;")
    except sqlite3.OperationalError:
        # Se a coluna já existe, ignora o erro
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


# ---------------- LAYOUT SIMPLES ----------------

def render_page(title: str, body_html: str) -> str:
    """Monta uma página HTML simples com o conteúdo passado."""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; background:#f5f5f5; margin:0; padding:0; }}
        .container {{ max-width: 800px; margin:40px auto; background:#fff; padding:20px;
                     border-radius:8px; box-shadow:0 0 10px rgba(0,0,0,0.1); }}
        h1, h2, h3 {{ text-align:center; }}
        form {{ display:flex; flex-direction:column; gap:10px; }}
        input[type=text], input[type=password] {{ padding:8px; border:1px solid #ccc; border-radius:4px; }}
        button {{ padding:8px 12px; border:none; border-radius:4px; cursor:pointer;
                 background:#007bff; color:#fff; font-weight:bold; }}
        button:hover {{ background:#0056b3; }}
        table {{ width:100%; border-collapse:collapse; margin-top:20px; font-size:14px; }}
        th, td {{ border:1px solid #ddd; padding:8px; text-align:left; vertical-align:top; }}
        th {{ background:#f0f0f0; }}
        .msg {{ padding:8px; background:#e7f3ff; border:1px solid #b3d7ff; border-radius:4px; margin-bottom:10px; }}
        .erro {{ padding:8px; background:#ffe5e5; border:1px solid #ffb3b3; border-radius:4px; margin-bottom:10px; }}
        .btn-danger {{ background:#ff4444; color:#fff; }}
        .btn-warning {{ background:#ff8800; color:#fff; }}
        code {{ font-size:12px; word-break:break-all; }}
    </style>
</head>
<body>
<div class="container">
{body_html}
</div>
</body>
</html>
"""


# ---------------- AUXILIARES ----------------

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


def get_client_ip():
    """Tenta pegar o IP real do cliente, considerando proxy do Render."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


# ---------------- GARANTIR QUE O BANCO EXISTA (LOCAL E RENDER) ----------------

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
            botoes += '<p><a href="/admin/dashboard"><button>Painel do Administrador</button></a></p>'
        else:
            botoes += '<p><a href="/driver/painel"><button>Painel do Motorista</button></a></p>'
        botoes += '<p><a href="/logout"><button>Sair</button></a></p>'

        body = f"""
        <h1>Sistema de Avaliação de Entregadores</h1>
        <p style="text-align:center;">Logado como: <strong>{user['name']} ({role_texto})</strong></p>
        {botoes}
        """
    else:
        body = """
        <h1>Sistema de Avaliação de Entregadores</h1>
        <h3>Escolha o tipo de login</h3>
        <p><a href="/admin/login"><button>Login Administrador</button></a></p>
        <p><a href="/driver/login"><button>Login Motorista</button></a></p>
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
    <h1>Login Administrador</h1>
    {msg_html}
    <form method="post">
        <label>Usuário</label>
        <input type="text" name="username" value="FARMALIMA">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit">Entrar</button>
    </form>
    <p><a href="/"><button>Voltar</button></a></p>
    """
    return render_page("Login Admin", body)


# ----- PAINEL ADMIN -----

@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    db = get_db()
    cur = db.execute("""
        SELECT u.id, u.name,
               COUNT(r.id) AS total_avaliacoes,
               COALESCE(ROUND(AVG(r.score), 2), 0) AS media
        FROM users u
        LEFT JOIN ratings r ON u.id = r.driver_id
        WHERE u.role = 'driver'
        GROUP BY u.id, u.name
        ORDER BY u.name;
    """)
    drivers = cur.fetchall()

    linhas = ""
    base_url = request.url_root.rstrip("/")
    for d in drivers:
        link_avaliacao = f"{base_url}{url_for('rate_driver', driver_id=d['id'])}"
        linhas += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{d['media']}</td>
            <td>{d['total_avaliacoes']}</td>
            <td><code>{link_avaliacao}</code></td>
            <td>
                <form style="display:inline;" method="post" action="/admin/reset_ratings/{d['id']}">
                    <button class="btn-warning"
                        onclick="return confirm('Zerar as avaliações deste motorista?');">
                        Zerar
                    </button>
                </form>
                <form style="display:inline;" method="post" action="/admin/delete_driver/{d['id']}">
                    <button class="btn-danger"
                        onclick="return confirm('EXCLUIR este motorista e todas as avaliações dele?');">
                        Excluir
                    </button>
                </form>
            </td>
        </tr>
        """

    body = f"""
    <h1>Painel do Administrador</h1>
    <p><strong>Admin:</strong> {current_user()['name']}</p>

    <h3>Cadastrar novo motorista</h3>
    <form method="post" action="/admin/create_driver">
        <label>Nome do motorista</label>
        <input type="text" name="name" required>
        <label>Usuário para login do motorista</label>
        <input type="text" name="username" required>
        <label>Senha para login do motorista</label>
        <input type="password" name="password" required>
        <button type="submit">Cadastrar Motorista</button>
    </form>

    <h3>Motoristas cadastrados</h3>
    <table>
        <tr>
            <th>Nome</th>
            <th>Média (1-5)</th>
            <th>Nº Avaliações</th>
            <th>Link de Avaliação</th>
            <th>Ações</th>
        </tr>
        {linhas}
    </table>

    <form method="post" action="/admin/reset_all_ratings">
        <button class="btn-danger" style="margin-top:20px; width:100%;"
            onclick="return confirm('ZERAR TODAS as avaliações do sistema?');">
            Zerar TODAS avaliações
        </button>
    </form>

    <p style="margin-top:20px;">
        <a href="/"><button>Voltar</button></a>
        <a href="/logout"><button>Sair</button></a>
    </p>
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
    <h1>Login Motorista</h1>
    {msg_html}
    <form method="post">
        <label>Usuário</label>
        <input type="text" name="username">
        <label>Senha</label>
        <input type="password" name="password">
        <button type="submit">Entrar</button>
    </form>
    <p><a href="/"><button>Voltar</button></a></p>
    """
    return render_page("Login Motorista", body)


# ----- PAINEL MOTORISTA (QR CODE) -----

@app.route("/driver/painel")
@login_required(role="driver")
def driver_panel():
    user = current_user()
    rate_url = request.url_root.rstrip('/') + url_for("rate_driver", driver_id=user["id"])
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={rate_url}"

    body = f"""
    <h1>Painel do Motorista</h1>
    <p>Olá, <strong>{user['name']}</strong></p>
    <p>Peça para o cliente apontar a câmera do celular para este QR Code:</p>
    <div style="text-align:center;">
        <img src="{qr_url}" alt="QR Code">
    </div>
    <p>Ou envie este link diretamente:</p>
    <p><code>{rate_url}</code></p>
    <p style="margin-top:20px;">
        <a href="/"><button>Voltar</button></a>
        <a href="/logout"><button>Sair</button></a>
    </p>
    """
    return render_page("Painel Motorista", body)


# ----- PÁGINA DE AVALIAÇÃO (COM ANTIFRAUDE POR IP) -----

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

            if score < 1 or score > 5:
                msg = "Selecione uma nota entre 1 e 5."
            else:
                db.execute(
                    "INSERT INTO ratings (driver_id, score, ip) VALUES (?, ?, ?)",
                    (driver_id, score, ip),
                )
                db.commit()
                body = f"""
                <h1>Obrigado!</h1>
                <p>Sua avaliação foi registrada com sucesso.</p>
                <p>Motorista: <strong>{driver['name']}</strong></p>
                """
                return render_page("Obrigado", body)

    msg_html = f'<div class="erro">{msg}</div>' if msg else ""
    body = f"""
    <h1>Avaliação do Entregador</h1>
    <p>Motorista: <strong>{driver['name']}</strong></p>
    <p><strong>Pergunta:</strong></p>
    <p>"Como você avalia meu atendimento e meu tempo de entrega?"</p>
    {msg_html}
    <form method="post">
        <label>Selecione de 1 a 5 estrelas:</label>
        <div style="font-size:24px;">
            <button name="score" value="1">⭐</button>
            <button name="score" value="2">⭐⭐</button>
            <button name="score" value="3">⭐⭐⭐</button>
            <button name="score" value="4">⭐⭐⭐⭐</button>
            <button name="score" value="5">⭐⭐⭐⭐⭐</button>
        </div>
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
    # Localmente, garante que o banco exista antes de rodar
    init_db()
    app.run(debug=True)
