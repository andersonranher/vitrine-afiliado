import os
import json
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave_secreta_vitrine_afiliado")

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
    
    # Cria a tabela base se não existir
    if is_pg:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id SERIAL PRIMARY KEY,
                titulo TEXT NOT NULL,
                preco TEXT NOT NULL,
                imagem_url TEXT NOT NULL,
                link_afiliado TEXT NOT NULL,
                categoria TEXT,
                vendas INTEGER DEFAULT 0,
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
                vendas INTEGER DEFAULT 0,
                descricao TEXT,
                especificacoes TEXT,
                desconto TEXT,
                pagamento TEXT,
                marca TEXT,
                modelo TEXT
            );
        """)
    
    # Garante a criação de novas colunas caso a tabela já existisse antes
    colunas_novas = [
        ("desconto", "TEXT"),
        ("pagamento", "TEXT"),
        ("marca", "TEXT"),
        ("modelo", "TEXT")
    ]
    for col, tipo in colunas_novas:
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
        "pagamento": "Pix, Boleto e Cartão",
        "marca": "",
        "modelo": "",
        "categoria": "Geral",
        "vendas": 0,
        "imagem_url": "",
        "especificacoes": {}
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

        # 2. Imagem em Alta Resolução (Sem corte ou links quebrados)
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            dados["imagem_url"] = og_img["content"]
        else:
            img = soup.find("img", class_=lambda c: c and "gallery" in c)
            if img:
                dados["imagem_url"] = img.get("src") or img.get("data-src") or ""

        # 3. Preço com Centavos Exatos
        # Pega a primeira área de preço principal (para não pegar o preço riscado anterior)
        container_preco = soup.find("span", class_="andes-money-amount ui-pdp-price__part") or soup
        fraction = container_preco.find("span", class_="andes-money-amount__fraction")
        cents = container_preco.find("span", class_="andes-money-amount__cents")
        
        if fraction:
            val = fraction.get_text(strip=True)
            if cents:
                val += f",{cents.get_text(strip=True)}"
            dados["preco"] = val
        else:
            meta_price = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
            if meta_price and meta_price.get("content"):
                dados["preco"] = meta_price["content"].replace(".", ",")

        # 4. Desconto (ex: 15% OFF)
        disc_span = soup.find("span", class_=lambda c: c and "discount" in c)
        if disc_span:
            dados["desconto"] = disc_span.get_text(strip=True)

        # 5. Parcelamento e Formas de Pagamento
        installments = soup.find(class_=lambda c: c and ("installments" in c or "payment-sub" in c))
        if installments:
            dados["pagamento"] = installments.get_text(" ", strip=True)
        else:
            dados["pagamento"] = "Pix com aprovação imediata ou até 12x no cartão"

        # 6. Quantidade de Vendas
        txt_pagina = soup.get_text()
        m_vendas = re.search(r"(\+?\d+[\.\d]*)\s*(mil)?\s*vendidos?", txt_pagina, re.IGNORECASE)
        if m_vendas:
            raw_v = m_vendas.group(1).replace(".", "")
            num = int(re.sub(r"\D", "", raw_v))
            if m_vendas.group(2):
                num *= 1000
            dados["vendas"] = num

        # 7. Categoria
        breadcrumbs = soup.find_all("a", class_=lambda c: c and "breadcrumb" in c)
        if breadcrumbs and len(breadcrumbs) > 1:
            dados["categoria"] = breadcrumbs[-1].get_text(strip=True)

        # 8. Marca e Modelo (extraídos da ficha técnica do Mercado Livre)
        for tr in soup.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                chave = th.get_text(strip=True).lower()
                valor = td.get_text(strip=True)
                if "marca" in chave:
                    dados["marca"] = valor
                elif "modelo" in chave:
                    dados["modelo"] = valor

    except Exception as e:
        print(f"Erro no scraper: {e}")

    return dados

# Rota chamada pelo Javascript do painel
@app.route("/api/extrair-dados", methods=["POST"])
def api_extrair_dados():
    payload = request.get_json() or {}
    url = payload.get("url", "").strip()
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
    produtos = cursor.fetchall()

    cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL AND categoria != '';")
    categorias_rows = cursor.fetchall()
    categorias = [r["categoria"] if isinstance(r, dict) else r[0] for r in categorias_rows]

    cursor.close()
    conn.close()

    produtos_formatados = [dict(p) for p in produtos]

    return render_template(
        "index.html",
        produtos=produtos_formatados,
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
        
    resultado = cursor.fetchone()
    cursor.close()
    conn.close()

    if resultado:
        link = resultado["link_afiliado"] if isinstance(resultado, dict) else resultado[0]
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
        imagem_url = request.form.get("imagem_url", "").strip()
        desconto = request.form.get("desconto", "").strip()
        pagamento = request.form.get("pagamento", "").strip()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        vendas = request.form.get("vendas", 0)
        try:
            vendas = int(vendas)
        except:
            vendas = 0

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
        imagem_url = request.form.get("imagem_url", "").strip()
        link_afiliado = request.form.get("link_afiliado", "").strip()
        desconto = request.form.get("desconto", "").strip()
        pagamento = request.form.get("pagamento", "").strip()
        marca = request.form.get("marca", "").strip()
        modelo = request.form.get("modelo", "").strip()
        vendas = request.form.get("vendas", 0)
        try:
            vendas = int(vendas)
        except:
            vendas = 0

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

if __name__ == "__main__":
    app.run(debug=True)
