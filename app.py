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
                especificacoes TEXT
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
                especificacoes TEXT
            );
        """)
    conn.commit()
    cursor.close()
    conn.close()

init_db()

def buscar_dados_mercadolivre(url):
    dados = {
        "titulo": "",
        "preco": "",
        "imagem_url": "",
        "vendas": 0,
        "descricao": "",
        "especificacoes": {}
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        session = requests.Session()
        # Se for link do encurtador meli.la, resolve o redirecionamento
        res = session.get(url, headers=headers, allow_redirects=True, timeout=12)
        url_final = res.url
        
        soup = BeautifulSoup(res.content, "html.parser")

        # 1. Título
        h1 = soup.find("h1", class_=lambda c: c and ("title" in c or "header" in c))
        if h1:
            dados["titulo"] = h1.get_text(strip=True)
        else:
            meta_title = soup.find("meta", property="og:title")
            if meta_title and meta_title.get("content"):
                dados["titulo"] = meta_title["content"].split(" | ")[0].strip()

        # 2. Imagem
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            dados["imagem_url"] = meta_img["content"]
        else:
            img = soup.find("img", class_=lambda c: c and "gallery" in c)
            if img:
                dados["imagem_url"] = img.get("src") or img.get("data-src") or ""

        # 3. Preço
        fraction = soup.find("span", class_="andes-money-amount__fraction")
        cents = soup.find("span", class_="andes-money-amount__cents")
        if fraction:
            dados["preco"] = fraction.get_text(strip=True)
            if cents:
                dados["preco"] += f",{cents.get_text(strip=True)}"
        else:
            meta_price = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
            if meta_price and meta_price.get("content"):
                dados["preco"] = meta_price["content"].replace(".", ",")

        # 4. Vendas
        texto_pagina = soup.get_text()
        match_vendas = re.search(r"(\+?\d+)\s*(mil)?\s*vendidos?", texto_pagina, re.IGNORECASE)
        if match_vendas:
            num = int(re.sub(r"\D", "", match_vendas.group(1)))
            if match_vendas.group(2):
                num *= 1000
            dados["vendas"] = num

    except Exception as e:
        print(f"Erro ao capturar dados do Mercado Livre: {e}")

    return dados

# Atende todas as possíveis rotas chamadas pelo JavaScript do botão
@app.route("/buscar-dados", methods=["GET", "POST"])
@app.route("/api/buscar-dados", methods=["GET", "POST"])
@app.route("/api/buscar", methods=["GET", "POST"])
def rota_buscar():
    url = request.args.get("url") or (request.json.get("url") if request.is_json else None) or request.form.get("url")
    if not url:
        return jsonify({"erro": "URL não enviada"}), 400

    dados = buscar_dados_mercadolivre(url)
    if not dados["titulo"] and not dados["preco"]:
        return jsonify({"erro": "Não foi possível extrair dados automaticamente deste link"}), 422

    return jsonify(dados)

@app.route("/")
def index():
    categoria = request.args.get("categoria")
    conn, is_pg = get_db_connection()
    
    if is_pg:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        if categoria:
            cursor.execute("SELECT * FROM produtos WHERE categoria = %s ORDER BY id DESC;", (categoria,))
        else:
            cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
        produtos = cursor.fetchall()

        cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL AND categoria != '';")
        categorias = [row["categoria"] for row in cursor.fetchall()]
    else:
        cursor = conn.cursor()
        if categoria:
            cursor.execute("SELECT * FROM produtos WHERE categoria = ? ORDER BY id DESC;", (categoria,))
        else:
            cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
        produtos = cursor.fetchall()

        cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL AND categoria != '';")
        categorias = [row["categoria"] for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    produtos_formatados = []
    for p in produtos:
        item = dict(p)
        produtos_formatados.append(item)

    return render_template(
        "index.html",
        produtos=produtos_formatados,
        categorias=categorias,
        categoria_ativa=categoria
    )

@app.route("/comprar/<int:produto_id>")
def comprar(produto_id):
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    
    if is_pg:
        cursor.execute("SELECT link_afiliado FROM produtos WHERE id = %s;", (produto_id,))
    else:
        cursor.execute("SELECT link_afiliado FROM produtos WHERE id = ?;", (produto_id,))
        
    resultado = cursor.fetchone()
    cursor.close()
    conn.close()

    if resultado:
        link = resultado[0] if not isinstance(resultado, dict) else resultado["link_afiliado"]
        return redirect(link)
    return "Produto não encontrado", 404

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        link_afiliado = request.form.get("link_afiliado", "").strip()
        link_produto = request.form.get("link_produto", "").strip() or link_afiliado
        categoria = request.form.get("categoria", "").strip() or "Geral"

        if not link_afiliado:
            flash("Informe o link de afiliado!", "error")
            return redirect(url_for("admin"))

        titulo = request.form.get("titulo", "").strip()
        preco = request.form.get("preco", "").strip()
        imagem_url = request.form.get("imagem_url", "").strip()
        vendas = request.form.get("vendas")
        descricao = request.form.get("descricao", "").strip()

        # Caso o botão automático não tenha sido usado, tenta extrair agora
        if not (titulo and preco and imagem_url):
            dados = buscar_dados_mercadolivre(link_produto)
            titulo = titulo or dados["titulo"] or "Produto Recomendado"
            preco = preco or dados["preco"] or "0,00"
            imagem_url = imagem_url or dados["imagem_url"]
            vendas = int(vendas) if vendas and vendas.isdigit() else dados["vendas"]
        else:
            vendas = int(vendas) if vendas and vendas.isdigit() else 0

        conn, is_pg = get_db_connection()
        cursor = conn.cursor()

        if is_pg:
            cursor.execute("""
                INSERT INTO produtos (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, json.dumps({})))
        else:
            cursor.execute("""
                INSERT INTO produtos (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, json.dumps({})))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Produto cadastrado com sucesso!", "success")
        return redirect(url_for("admin"))

    conn, is_pg = get_db_connection()
    if is_pg:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
        produtos = cursor.fetchall()
    else:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM produtos ORDER BY id DESC;")
        produtos = cursor.fetchall()
        
    cursor.close()
    conn.close()

    return render_template("admin.html", produtos=produtos)

if __name__ == "__main__":
    app.run(debug=True)
