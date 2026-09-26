import os
import json
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave_secreta_vitrine_afiliado")

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        # Corrige prefixo postgres:// caso o provedor retorne com essa sintaxe
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

# Inicializa o banco ao subir a aplicação
init_db()

def buscar_dados_mercadolivre(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    dados = {
        "titulo": "",
        "preco": "0.00",
        "imagem_url": "",
        "vendas": 0,
        "descricao": "",
        "especificacoes": {}
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return dados

        soup = BeautifulSoup(resp.content, "html.parser")

        # Título
        h1 = soup.find("h1", class_=lambda c: c and "title" in c)
        if h1:
            dados["titulo"] = h1.get_text(strip=True)

        # Preço
        price_tag = soup.find("span", class_="andes-money-amount__fraction")
        if price_tag:
            dados["preco"] = price_tag.get_text(strip=True)

        # Imagem
        img_tag = soup.find("img", class_=lambda c: c and "gallery" in c) or soup.find("img", {"decoding": "async"})
        if img_tag:
            dados["imagem_url"] = img_tag.get("src") or img_tag.get("data-src", "")

        # Vendas (ex: "+1000 vendidos", "+50 mil vendidos")
        subtitle = soup.find(class_=lambda c: c and "subtitle" in c)
        if subtitle:
            texto_vendas = subtitle.get_text()
            match = re.search(r"(\d+)\s*(mil)?\s*vendidos?", texto_vendas, re.IGNORECASE)
            if match:
                qtd = int(match.group(1))
                if match.group(2):
                    qtd *= 1000
                dados["vendas"] = qtd

        # Descrição simples
        desc_p = soup.find("p", class_=lambda c: c and "description" in c)
        if desc_p:
            dados["descricao"] = desc_p.get_text(strip=True)

    except Exception as e:
        print(f"Erro ao capturar dados do ML: {e}")

    return dados

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

    # Formatar JSON de especificações se existir
    produtos_formatados = []
    for p in produtos:
        item = dict(p)
        if item.get("especificacoes"):
            try:
                item["especificacoes"] = json.loads(item["especificacoes"])
            except:
                item["especificacoes"] = {}
        else:
            item["especificacoes"] = {}
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

        dados = buscar_dados_mercadolivre(link_produto)

        # Campos manuais sobrescrevem o scraper se preenchidos
        titulo = request.form.get("titulo", "").strip() or dados["titulo"] or "Produto Recomendado"
        preco = request.form.get("preco", "").strip() or dados["preco"] or "0,00"
        imagem_url = request.form.get("imagem_url", "").strip() or dados["imagem_url"]
        vendas = request.form.get("vendas")
        vendas = int(vendas) if vendas and vendas.isdigit() else dados["vendas"]
        descricao = request.form.get("descricao", "").strip() or dados["descricao"]

        conn, is_pg = get_db_connection()
        cursor = conn.cursor()

        if is_pg:
            cursor.execute("""
                INSERT INTO produtos (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, json.dumps(dados["especificacoes"])))
        else:
            cursor.execute("""
                INSERT INTO produtos (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, especificacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (titulo, preco, imagem_url, link_afiliado, categoria, vendas, descricao, json.dumps(dados["especificacoes"])))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Produto cadastrado com sucesso!", "success")
        return redirect(url_for("admin"))

    # Listar produtos já cadastrados no painel
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
