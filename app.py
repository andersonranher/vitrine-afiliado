import sqlite3
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for, abort

app = Flask(__name__)
DB_NAME = "vitrine.db"

def classificar_texto(texto):
    t = texto.lower()
    if any(k in t for k in ["scanner", "multímetro", "osciloscópio", "chave", "torquímetro", "ferramenta"]):
        return "Ferramentas e Diagnóstico"
    elif any(k in t for k in ["bico", "injetor", "flauta", "bomba alta", "sensor", "atuador", "ecu", "módulo"]):
        return "Injeção Eletrônica e Sensores"
    elif any(k in t for k in ["pistão", "biela", "cabeçote", "válvula", "junta", "correia", "tensor", "motor"]):
        return "Motor e Componentes"
    elif any(k in t for k in ["óleo", "aditivo", "fluido", "graxa", "limpa contato", "descarbonizante"]):
        return "Fluidos e Lubrificantes"
    elif any(k in t for k in ["led", "farol", "palheta", "capa", "suporte"]):
        return "Acessórios Automotivos"
    return "Geral"

def extrair_categoria_automatica(url_afiliado, titulo_digitado=""):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }
        resposta = requests.get(url_afiliado, headers=headers, timeout=5, allow_redirects=True)
        if resposta.status_code == 200:
            soup = BeautifulSoup(resposta.text, 'html.parser')
            breadcrumbs = soup.find_all("a", class_="andes-breadcrumb__link")
            if breadcrumbs and len(breadcrumbs) > 1:
                return breadcrumbs[1].get_text().strip()
            
            tag_titulo = soup.find("h1") or soup.find("title")
            if tag_titulo:
                return classificar_texto(tag_titulo.get_text())
    except Exception:
        pass

    return classificar_texto(titulo_digitado)

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                descricao TEXT,
                preco TEXT,
                imagem_url TEXT,
                categoria TEXT,
                link_afiliado TEXT NOT NULL,
                cliques INTEGER DEFAULT 0
            )
        """)
        try:
            cursor.execute("ALTER TABLE produtos ADD COLUMN categoria TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()

init_db()

@app.route('/')
def index():
    categoria_filtro = request.args.get('categoria')
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT categoria FROM produtos WHERE categoria IS NOT NULL AND categoria != ''")
        categorias = [row['categoria'] for row in cursor.fetchall()]

        if categoria_filtro:
            cursor.execute("SELECT * FROM produtos WHERE categoria = ? ORDER BY id DESC", (categoria_filtro,))
        else:
            cursor.execute("SELECT * FROM produtos ORDER BY id DESC")
        produtos = cursor.fetchall()

    return render_template('index.html', produtos=produtos, categorias=categorias, categoria_ativa=categoria_filtro)

@app.route('/comprar/<int:produto_id>')
def comprar(produto_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT link_afiliado FROM produtos WHERE id = ?", (produto_id,))
        produto = cursor.fetchone()
        
        if produto:
            cursor.execute("UPDATE produtos SET cliques = cliques + 1 WHERE id = ?", (produto_id,))
            conn.commit()
            return redirect(produto[0])
            
    abort(404)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        preco = request.form['preco']
        imagem_url = request.form['imagem_url']
        link_afiliado = request.form['link_afiliado']

        categoria_detectada = extrair_categoria_automatica(link_afiliado, titulo)

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO produtos (titulo, descricao, preco, imagem_url, categoria, link_afiliado)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (titulo, descricao, preco, imagem_url, categoria_detectada, link_afiliado))
            conn.commit()

        return redirect(url_for('admin'))

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM produtos ORDER BY id DESC")
        produtos = cursor.fetchall()

    return render_template('admin.html', produtos=produtos)

if __name__ == '__main__':
    app.run(debug=True)