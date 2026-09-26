import sqlite3
import json
import re
import os
import time
import threading
import requests
import schedule
from bs4 import BeautifulSoup
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort

def obter_vendas_ml(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resposta = requests.get(url, headers=headers, timeout=10)
        if resposta.status_code == 200:
            sopa = BeautifulSoup(resposta.text, "html.parser")
            
            # Procura pelo texto indicativo de vendas no anúncio
            elemento_vendas = sopa.find("span", class_="ui-pdp-subtitle")
            if elemento_vendas:
                texto = elemento_vendas.get_text()
                # Exemplo de texto capturado: "Novo  |  +1000 vendidos"
                numeros = re.findall(r"\d+", texto.replace(".", ""))
                if numeros:
                    return int(numeros[-1])
    except Exception as e:
        print(f"Erro ao capturar vendas: {e}")
    return 0

app = Flask(__name__)
DB_NAME = "vitrine.db"
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

HEADERS_PADRAO = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9"
}

def arquivo_permitido(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def processar_imagem(req_files, form_data, imagem_atual=""):
    """Trata o envio de imagem: dá prioridade ao arquivo de upload; se não houver, usa o link digitado."""
    if 'imagem_upload' in req_files:
        arquivo = req_files['imagem_upload']
        if arquivo and arquivo.filename != '' and arquivo_permitido(arquivo.filename):
            nome_seguro = f"{int(time.time())}_{secure_filename(arquivo.filename)}"
            caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_seguro)
            arquivo.save(caminho_salvar)
            return f"/static/uploads/{nome_seguro}"
    
    imagem_url = form_data.get('imagem_url', '').strip()
    if imagem_url:
        return imagem_url
    return imagem_atual

def extrair_dados_completos_ml(url_afiliado):
    dados = {
        "titulo": "",
        "preco": "",
        "imagem_url": "",
        "categoria": "Geral",
        "especificacoes": {}
    }
    try:
        session = requests.Session()
        resposta = session.get(url_afiliado, headers=HEADERS_PADRAO, timeout=12, allow_redirects=True)
        if resposta.status_code != 200:
            return dados
            
        soup = BeautifulSoup(resposta.text, 'html.parser')
        
        # 1. Título
        tag_titulo = soup.find("meta", property="og:title")
        if tag_titulo and tag_titulo.get("content"):
            dados["titulo"] = tag_titulo["content"].strip()
        elif soup.find("h1"):
            dados["titulo"] = soup.find("h1").get_text().strip()

        # 2. Imagem
        tag_img = soup.find("meta", property="og:image")
        if tag_img and tag_img.get("content"):
            dados["imagem_url"] = tag_img["content"].strip()
        if not dados["imagem_url"]:
            img_tag = soup.find("img", class_=re.compile(r"ui-pdp-image|ui-pdp-gallery__figure__image"))
            if img_tag:
                dados["imagem_url"] = img_tag.get("data-zoom") or img_tag.get("src") or ""

        # 3. Preço
        tag_preco = soup.find("meta", itemprop="price")
        if tag_preco and tag_preco.get("content"):
            dados["preco"] = tag_preco["content"].replace(".", ",")
        else:
            preco_frac = soup.find("span", class_="andes-money-amount__fraction")
            if preco_frac:
                dados["preco"] = preco_frac.get_text().strip()

        # 4. Categoria
        scripts_json = soup.find_all("script", type="application/ld+json")
        for s in scripts_json:
            try:
                conteudo = json.loads(s.string)
                if isinstance(conteudo, dict) and conteudo.get("@type") == "BreadcrumbList":
                    itens = conteudo.get("itemListElement", [])
                    if len(itens) > 1:
                        dados["categoria"] = itens[1].get("item", {}).get("name") or itens[1].get("name", "Geral")
                        break
            except Exception:
                continue

        if dados["categoria"] == "Geral":
            breadcrumbs = soup.find_all("a", class_=re.compile(r"andes-breadcrumb__link"))
            if breadcrumbs and len(breadcrumbs) > 1:
                dados["categoria"] = breadcrumbs[1].get_text().strip()
            elif breadcrumbs:
                dados["categoria"] = breadcrumbs[0].get_text().strip()

        # 5. Características Técnicas
        tabelas = soup.find_all("table", class_=re.compile(r"andes-table"))
        for tab in tabelas:
            for tr in tab.find_all("tr"):
                th = tr.find(["th", "span", "div"], class_=re.compile(r"header|col-header|key"))
                td = tr.find(["td", "span", "div"], class_=re.compile(r"value|col-value"))
                if th and td:
                    k = th.get_text().strip()
                    v = td.get_text().strip()
                    if k and v:
                        dados["especificacoes"][k] = v

    except Exception as e:
        print(f"Erro na extração: {e}")

    return dados

def capturar_preco_ml(url_afiliado):
    try:
        session = requests.Session()
        resp = session.get(url_afiliado, headers=HEADERS_PADRAO, timeout=8, allow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            tag_preco = soup.find("meta", itemprop="price")
            if tag_preco and tag_preco.get("content"):
                return tag_preco["content"].replace(".", ",")
            preco_frac = soup.find("span", class_="andes-money-amount__fraction")
            if preco_frac:
                return preco_frac.get_text().strip()
    except Exception:
        pass
    return None

def sincronizar_todos_os_precos():
    print("[ROBÔ] Atualizando preços...")
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, link_afiliado, preco, titulo FROM produtos")
        for item_id, link, preco_velho, titulo in cursor.fetchall():
            novo_preco = capturar_preco_ml(link)
            if novo_preco and novo_preco != preco_velho:
                cursor.execute("UPDATE produtos SET preco = ? WHERE id = ?", (novo_preco, item_id))
                print(f"[ATUALIZADO] {titulo[:25]} -> R$ {novo_preco}")
            time.sleep(1)
        conn.commit()

def loop_agendador():
    schedule.every().day.at("04:00").do(sincronizar_todos_os_precos)
    schedule.every(6).hours.do(sincronizar_todos_os_precos)
    while True:
        schedule.run_pending()
        time.sleep(30)

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
                especificacoes TEXT,
                link_afiliado TEXT NOT NULL,
                cliques INTEGER DEFAULT 0
            )
        """)
        conn.commit()

init_db()

thread_robo = threading.Thread(target=loop_agendador, daemon=True)
thread_robo.start()

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
        produtos = []
        for p in cursor.fetchall():
            item = dict(p)
            try:
                item['especificacoes'] = json.loads(p['especificacoes']) if p['especificacoes'] else {}
            except Exception:
                item['especificacoes'] = {}
            produtos.append(item)

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

@app.route('/api/extrair-dados', methods=['POST'])
def api_extrair():
    req = request.get_json() or {}
    url = req.get('url', '').strip()
    if not url:
        return jsonify({"erro": "URL vazia"}), 400
    dados = extrair_dados_completos_ml(url)
    return jsonify(dados)

@app.route('/admin/atualizar-precos', methods=['POST'])
def rota_atualizar_precos():
    sincronizar_todos_os_precos()
    return redirect(url_for('admin'))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        preco = request.form['preco']
        categoria = request.form.get('categoria', 'Geral')
        especificacoes = request.form.get('especificacoes', '{}')
        link_afiliado = request.form['link_afiliado']
        imagem_final = processar_imagem(request.files, request.form)

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO produtos (titulo, descricao, preco, imagem_url, categoria, especificacoes, link_afiliado)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (titulo, descricao, preco, imagem_final, categoria, especificacoes, link_afiliado))
            conn.commit()

        return redirect(url_for('admin'))

    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM produtos ORDER BY id DESC")
        produtos = cursor.fetchall()

    return render_template('admin.html', produtos=produtos)

# ROTA DE EDIÇÃO DE PRODUTO
@app.route('/admin/editar/<int:produto_id>', methods=['GET', 'POST'])
def editar_produto(produto_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM produtos WHERE id = ?", (produto_id,))
        produto = cursor.fetchone()

    if not produto:
        abort(404)

    if request.method == 'POST':
        titulo = request.form['titulo']
        descricao = request.form['descricao']
        preco = request.form['preco']
        categoria = request.form.get('categoria', 'Geral')
        link_afiliado = request.form['link_afiliado']
        imagem_final = processar_imagem(request.files, request.form, produto['imagem_url'])

        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE produtos 
                SET titulo = ?, descricao = ?, preco = ?, imagem_url = ?, categoria = ?, link_afiliado = ?
                WHERE id = ?
            """, (titulo, descricao, preco, imagem_final, categoria, link_afiliado, produto_id))
            conn.commit()

        return redirect(url_for('admin'))

    return render_template('editar.html', p=produto)

# ROTA DE EXCLUSÃO DE PRODUTO
@app.route('/admin/excluir/<int:produto_id>', methods=['POST'])
def excluir_produto(produto_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM produtos WHERE id = ?", (produto_id,))
        conn.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
