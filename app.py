import os
import json
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave_secreta_vitrine_afiliado")

# Configuração de uploads locais
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        uri = DATABASE_URL
        if uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(uri)
        return conn, True
    else:
        conn = sqlite3.connect("vitrine.db")
        conn.row_factory = sqlite3.Row
        return conn, False

def init_db():
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    
    if is_pg:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                titulo TEXT NOT NULL,
                preco TEXT NOT NULL,
                imagem_url TEXT NOT NULL,
                link_afiliado TEXT NOT NULL,
                categoria TEXT,
                vendas TEXT DEFAULT '',
                descricao TEXT,
                especificacoes TEXT,
                desconto TEXT,
                pagamento TEXT,
                marca TEXT,
                modelo TEXT
            );
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                preco TEXT NOT NULL,
                imagem_url TEXT NOT NULL,
                link_afiliado TEXT NOT NULL,
                categoria TEXT,
                vendas TEXT DEFAULT '',
                descricao TEXT,
                especificacoes TEXT,
                desconto TEXT,
                pagamento TEXT,
                marca TEXT,
                modelo TEXT
            );
        """)
    
    # Atualiza a estrutura da base de dados caso colunas anteriores fossem inteiras ou faltassem
    colunas = [
        ("desconto", "TEXT"),
        ("pagamento", "TEXT"),
        ("marca", "TEXT"),
        ("modelo", "TEXT"),
        ("vendas", "TEXT")
    ]
    for col, tipo in colunas:
        try:
            cursor.execute(f"ALTER TABLE produtos ADD COLUMN {col} {tipo};")
            conn.commit()
        except Exception:
            conn.rollback()

    conn.commit()
    cursor.close()
    conn.close()

init_db()

def extrair_dados_ml(url_afiliado):
    dados = {
        "titulo": "",
        "preco": "0,00",
        "desconto": "",
        "pagamento": "Pix, Boleto e Cartão de Crédito",
        "marca": "",
        "modelo": "",
        "categoria": "Geral",
        "vendas": "",
        "imagem_url": ""
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        session = requests.Session()
        res = session.get(url_afiliado, headers=headers, allow_redirects=True, timeout=15)
        soup = BeautifulSoup(res.content, "html.parser")

        # 1. Título
        h1 = soup.find("h1", class_=lambda c: c and ("title" in c or "header" in c))
        if h1:
            dados["titulo"] = h1.get_text(strip=True)
        else:
            og_t = soup.find("meta", property="og:title")
            if og_t and og_t.get("content"):
                dados["titulo"] = og_t["content"].split(" | ")[0].strip()

        # 2. Imagem real em alta resolução
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            img_src = og_img["content"]
            # Remove variantes de tamanho pequeno e garante alta definição
            img_src = re.sub(r"-I\.jpg", "-O.jpg", img_src)
            img_src = re.sub(r"-V\.jpg", "-O.jpg", img_src)
            dados["imagem_url"] = img_src
        else:
            galeria = soup.find("img", class_=lambda c: c and "gallery" in c)
            if galeria:
                dados["imagem_url"] = galeria.get("data-zoom") or galeria.get("src") or ""

        # 3. Preço com centavos
        container_preco = soup.find("span", class_="andes-money-amount ui-pdp-price__part") or soup
        frac = container_preco.find("span", class_="andes-money-amount__fraction")
        cents = container_preco.find("span", class_="andes-money-amount__cents")
        if frac:
            p_val = frac.get_text(strip=True)
            if cents:
                p_val += f",{cents.get_text(strip=True)}"
            dados["preco"] = p_val
        else:
            meta_p = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
            if meta_p and meta_p.get("content"):
                dados["preco"] = meta_p["content"].replace(".", ",")

        # 4. Desconto
        disc = soup.find("span", class_=lambda c: c and "discount" in c)
        if disc:
            dados["desconto"] = disc.get_text(strip=True)

        # 5. Formas de Pagamento e Parcelas
        parcelas = soup.find(class_=lambda c: c and ("installments" in c or "payment-sub" in c))
        if parcelas:
            dados["pagamento"] = parcelas.get_text(" ", strip=True)

        # 6. Quantidade de Vendas (+500 vendidos)
        m_vendas = re.search(r"(\+?\d+[\.\d]*\s*(mil)?\s*vendidos?)", soup.get_text(), re.IGNORECASE)
        if m_vendas:
            dados["vendas"] = m_vendas.group(1).strip()
        else:
            sub = soup.find(class_=lambda c: c and "subtitle" in c)
            if sub and "vendido" in sub.get_text().lower():
                dados["vendas"] = sub.get_text(strip=True).split("|")[-1].strip()

        # 7. Categoria
        crumbs = soup.find_all("a", class_=lambda c: c and "breadcrumb" in c)
        if crumbs and len(crumbs) > 1:
            dados["categoria"] = crumbs[-1].get_text(strip=True)

        # 8. Marca e Modelo (Características Principais do Produto)
        tabelas = soup.find_all(["table", "div"], class_=lambda c: c and "specs" in c) or soup.find_all("table")
        for tabela in tabelas:
            for tr in tabela.find_all("tr"):
                th = tr.find(["th", "span", "div"], class_=lambda c: c and ("label" in c or "header" in c or "title" in c)) or tr.find("th")
                td = tr.find(["td", "span", "div"], class_=lambda c: c and "value" in c) or tr.find("td")
                if th and td:
                    rotulo = th.get_text(strip=True).lower()
                    conteudo = td.get_text(strip=True)
                    if "marca" in rotulo and not dados["marca"]:
                        dados["marca"] = conteudo
                    elif "modelo" in rotulo and not dados["modelo"]:
                        dados["modelo"] = conteudo

        # Busca suplementar por atributos de texto caso a tabela seja em listas
        if not dados["marca"] or not dados["modelo"]:
            for item in soup.find_all(class_=lambda c: c and "item-property" in c):
                t_item = item.get_text(strip=True).lower()
                if "marca:" in t_item and not dados["marca"]:
                    dados["marca"] = t_item.split("marca:")[-1].strip()
                elif "modelo:" in t_item and not dados["modelo"]:
                    dados["modelo"] = t_item.split("modelo:")[-1].strip()

    except Exception as e:
        print(f"Erro ao extrair dados do Mercado Livre: {e}")

    return dados

@app.route("/api/extrair-dados", methods=["POST"])
def api_extrair_dados():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"erro": "URL não fornecida"}), 400
    dados = extrair_dados_ml(url)
    return jsonify(dados), 200

@app.route("/")
def index():
    categoria = request.args.get("categoria")
    conn, is_pg = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) if is_pg else conn.cursor()

    if categoria:
        if is_pg:
            cursor.execute("SELECT * FROM produtos WHERE categoria = %s ORDER BY id DESC;", (categoria,))
        else:
            cursor.execute("SELECT * FROM produtos WHERE categoria = ? ORDER BY id DESC;", (categoria,))
    else:
        cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
    produtos = [dict(p) for p in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL AND categoria != '';")
    cat_rows = cursor.fetchall()
    categorias = [c["categoria"] if isinstance(c, dict) else c[0] for c in cat_rows]

    cursor.close()
    conn.close()

    return render_template(
        "index.html",
        produtos=produtos,
        categorias=categorias,
        categoria_ativa=categoria
    )

@app.route("/comprar/<int:produto_id>")
def comprar(produto_id):
    conn, is_pg = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) if is_pg else conn.cursor()

    if is_pg:
        cursor.execute("SELECT link_afiliado FROM produtos WHERE id = %s;", (produto_id,))
    else:
        cursor.execute("SELECT link_afiliado FROM produtos WHERE id = ?;", (produto_id,))
    res = cursor.fetchone()
    cursor.close()
    conn.close()

    if res:
        link = res["link_afiliado"] if isinstance(res, dict) else res[0]
        if link:
            return redirect(link)
    return "Produto não encontrado", 404

@app.route("/admin", methods=["GET", "POST"])
def admin():
    conn, is_pg = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) if is_pg else conn.cursor()

    if request.method == "POST":
        link_afiliado = request.form.get("link_afiliado", "").strip()
        titulo = request.form.get("titulo", "").strip()
        preco = request.form.get("preco", "").strip()
        categoria = request.form.get("categoria", "").strip() or "Geral"
        desconto = request.form.get("desconto", "").strip()
        pagamento = request.form.get("pagamento", "").strip()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        vendas = request.form.get("vendas", "").strip()

        # Tratamento de Imagem: Upload local ou Link web
        imagem_url = request.form.get("imagem_url", "").strip()
        file = request.files.get("imagem_upload")
        if file and file.filename != "" and allowed_file(file.filename):
            s_name = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], s_name))
            imagem_url = f"/static/uploads/{s_name}"

        if is_pg:
            cursor.execute("""
                INSERT INTO produtos 
                (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes, desconto, pagamento, marca, modelo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, "", "{}", desconto, pagamento, marca, modelo))
        else:
            cursor.execute("""
                INSERT INTO produtos 
                (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes, desconto, pagamento, marca, modelo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, "", "{}", desconto, pagamento, marca, modelo))

        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("admin"))

    cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
    produtos = [dict(p) for p in cursor.fetchall()]
    cursor.close()
    conn.close()
    return render_template("admin.html", produtos=produtos)

@app.route("/admin/editar/<int:produto_id>", methods=["GET", "POST"])
def editar_produto(produto_id):
    conn, is_pg = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) if is_pg else conn.cursor()

    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        preco = request.form.get("preco", "").strip()
        categoria = request.form.get("categoria", "").strip() or "Geral"
        link_afiliado = request.form.get("link_afiliado", "").strip()
        desconto = request.form.get("desconto", "").strip()
        pagamento = request.form.get("pagamento", "").strip()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        vendas = request.form.get("vendas", "").strip()

        imagem_url = request.form.get("imagem_url", "").strip()
        file = request.files.get("imagem_upload")
        if file and file.filename != "" and allowed_file(file.filename):
            s_name = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], s_name))
            imagem_url = f"/static/uploads/{s_name}"

        if is_pg:
            cursor.execute("""
                UPDATE produtos
                SET titulo=%s, preco=%s, imagem_url=%s, link_afiliado=%s, categoria=%s,
                    desconto=%s, pagamento=%s, marca=%s, modelo=%s, vendas=%s
                WHERE id=%s;
            """, (titulo, preco, imagem_url, link_afiliado, categoria, desconto, pagamento, marca, modelo, vendas, produto_id))
        else:
            cursor.execute("""
                UPDATE produtos
                SET titulo=?, preco=?, imagem_url=?, link_afiliado=?, categoria=?,
                    desconto=?, pagamento=?, marca=?, modelo=?, vendas=?
                WHERE id=?;
            """, (titulo, preco, imagem_url, link_afiliado, categoria, desconto, pagamento, marca, modelo, vendas, produto_id))

        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for("admin"))

    if is_pg:
        cursor.execute("SELECT * FROM produtos WHERE id = %s;", (produto_id,))
    else:
        cursor.execute("SELECT * FROM produtos WHERE id = ?;", (produto_id,))
    produto = cursor.fetchone()
    cursor.close()
    conn.close()

    if not produto:
        return "Produto não encontrado", 404

    return render_template("editar.html", p=dict(produto))

@app.route("/admin/excluir/<int:produto_id>", methods=["POST"])
def excluir_produto(produto_id):
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    if is_pg:
        cursor.execute("DELETE FROM produtos WHERE id = %s;", (produto_id,))
    else:
        cursor.execute("DELETE FROM produtos WHERE id = ?;", (produto_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for("admin"))

# Atualização em massa dos preços e dados de todos os produtos cadastrados
@app.route("/admin/atualizar-precos", methods=["POST"])
def atualizar_precos():
    conn, is_pg = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor) if is_pg else conn.cursor()
    cursor.execute("SELECT id, link_afiliado FROM produtos;")
    produtos = [dict(p) for p in cursor.fetchall()]

    for prod in produtos:
        if prod.get("link_afiliado"):
            novos = extrair_dados_ml(prod["link_afiliado"])
            if novos.get("preco") and novos["preco"] != "0,00":
                if is_pg:
                    cursor.execute("""
                        UPDATE produtos 
                        SET preco=%s, desconto=%s, pagamento=%s, vendas=%s
                        WHERE id=%s;
                    """, (novos["preco"], novos["desconto"], novos["pagamento"], novos["vendas"], prod["id"]))
                else:
                    cursor.execute("""
                        UPDATE produtos 
                        SET preco=?, desconto=?, pagamento=?, vendas=?
                        WHERE id=?;
                    """, (novos["preco"], novos["desconto"], novos["pagamento"], novos["vendas"], prod["id"]))
                conn.commit()

    cursor.close()
    conn.close()
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(debug=True)
