import os
import re
import shutil
from datetime import datetime

import fitz
import pytesseract
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from flask import Flask, render_template, request, redirect, session, send_file
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openpyxl import load_workbook

from agent.supabase_client import supabase


pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "agent-secret-key")

UPLOAD_FOLDER = "uploads"
EXPORT_FOLDER = "exports"
TEMPLATE_FOLDER = "templates_excel"

MODELO_COTACAO = os.path.join(TEMPLATE_FOLDER, "modelo_cotacao.xlsx")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)


def usuario_logado():
    return "usuario_nome" in session


def buscar_usuario(nome):
    resposta = supabase.table("usuarios").select("*").eq("nome", nome).execute()

    if resposta.data:
        return resposta.data[0]

    return None


def criar_usuario(nome, senha):
    senha_hash = generate_password_hash(senha)

    supabase.table("usuarios").insert({
        "nome": nome,
        "senha": senha_hash
    }).execute()


def buscar_historico(nome, limite=20):
    resposta = (
        supabase
        .table("tickets")
        .select("*")
        .eq("user_id", nome)
        .order("id", desc=False)
        .limit(limite)
        .execute()
    )

    return resposta.data


def responder_chat(mensagem, historico):
    mensagens = [
        {
            "role": "system",
            "content": (
                "Voce e um agente de IA amigavel e inteligente. "
                "Converse naturalmente. "
                "Lembre do contexto anterior. "
                "Responda de forma clara e util."
            )
        }
    ]

    for item in historico:
        mensagens.append({"role": "user", "content": item["ticket"]})
        mensagens.append({"role": "assistant", "content": item["categoria"]})

    mensagens.append({"role": "user", "content": mensagem})

    resposta = client.chat.completions.create(
        model="openrouter/free",
        messages=mensagens,
        temperature=0.7,
        max_tokens=300
    )

    conteudo = resposta.choices[0].message.content

    if conteudo is None:
        return "Nao consegui responder agora."

    return conteudo.strip()


def salvar_mensagem(nome, usuario, resposta):
    supabase.table("tickets").insert({
        "user_id": nome,
        "ticket": usuario,
        "categoria": resposta
    }).execute()


def preparar_imagem_para_ocr(caminho_imagem):
    imagem = Image.open(caminho_imagem)

    imagem = imagem.convert("L")
    imagem = ImageOps.autocontrast(imagem)
    imagem = ImageEnhance.Contrast(imagem).enhance(3)
    imagem = imagem.resize((imagem.width * 2, imagem.height * 2))
    imagem = imagem.filter(ImageFilter.SHARPEN)

    return imagem


def ler_texto_imagem(caminho_imagem):
    imagem = preparar_imagem_para_ocr(caminho_imagem)

    texto = pytesseract.image_to_string(
        imagem,
        lang="eng",
        config="--psm 6"
    )

    return texto


def ler_texto_pdf(caminho_pdf):
    texto_final = ""

    documento = fitz.open(caminho_pdf)

    for numero_pagina in range(len(documento)):
        pagina = documento[numero_pagina]

        texto_pagina = pagina.get_text()

        if texto_pagina and len(texto_pagina.strip()) > 30:
            texto_final += "\n" + texto_pagina

        else:
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2))

            nome_imagem = os.path.join(
                UPLOAD_FOLDER,
                "pagina_pdf_" + str(numero_pagina) + ".png"
            )

            pix.save(nome_imagem)

            texto_ocr = ler_texto_imagem(nome_imagem)

            texto_final += "\n" + texto_ocr

    documento.close()

    return texto_final


def ler_texto_arquivo(caminho_arquivo):
    extensao = os.path.splitext(caminho_arquivo)[1].lower()

    if extensao == ".pdf":
        return ler_texto_pdf(caminho_arquivo)

    return ler_texto_imagem(caminho_arquivo)


def limpar_codigo(codigo):
    codigo = codigo.strip().upper()
    codigo = codigo.replace("$", "S")
    codigo = codigo.replace(" ", "")
    codigo = re.sub(r"[^A-Z0-9\-]", "", codigo)

    return codigo


def eh_item_sequencial(valor):
    valor = valor.strip()

    if re.match(r"^0{0,3}\d{1,3}$", valor):
        numero = int(valor)
        if numero >= 1 and numero <= 999:
            return True

    return False


def eh_quantidade(valor):
    valor = valor.strip().replace(",", ".")

    try:
        numero = float(valor)
    except:
        return False

    if numero >= 1 and numero <= 99:
        return True

    return False


def eh_codigo(valor):
    valor = limpar_codigo(valor)

    if len(valor) < 4:
        return False

    if not re.search(r"\d", valor):
        return False

    if eh_item_sequencial(valor):
        return False

    palavras_ruins = [
        "2026",
        "005922",
        "18775",
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010"
    ]

    if valor in palavras_ruins:
        return False

    return True


def limpar_descricao(texto):
    texto = texto.upper()

    texto = texto.replace("|", " ")
    texto = texto.replace("_", " ")
    texto = texto.replace("-", " ")

    texto = re.sub(r"[^A-Z0-9 ]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    if not texto:
        return "FILTRO"

    return texto


def classificar_linha_cotacao(linha):
    linha_original = linha
    linha = linha.strip().upper()

    if not linha:
        return None

    tokens = re.findall(
        r"\d+,\d+|[A-Z0-9\-]+",
        linha
    )

    if not tokens:
        return None

    codigo = None
    qtd = None
    descricao_tokens = []

    for token in tokens:
        token_limpo = limpar_codigo(token)

        if token_limpo in [
            "ITEM",
            "QTD",
            "PRODUTO",
            "EQUIVALENCIA",
            "DESCRICAO",
            "DESCRICADO",
            "CODIGO"
        ]:
            continue

        if eh_item_sequencial(token_limpo):
            continue

        if qtd is None and eh_quantidade(token):
            qtd = token
            continue

        if codigo is None and eh_codigo(token_limpo):
            codigo = token_limpo
            continue

        descricao_tokens.append(token)

    if codigo is None:
        return None

    if qtd is None:
        qtd = "1"

    descricao = " ".join(descricao_tokens)
    descricao = descricao.replace(codigo, " ")
    descricao = limpar_descricao(descricao)

    if len(descricao) < 3:
        descricao = "FILTRO"

    return {
        "codigo": codigo,
        "qtd": qtd,
        "descricao": descricao
    }


def extrair_itens_do_texto(texto):
    itens = []
    codigos_usados = set()

    linhas = texto.splitlines()

    for linha in linhas:
        item = classificar_linha_cotacao(linha)

        if not item:
            continue

        codigo = item.get("codigo", "")

        if not codigo:
            continue

        if codigo in codigos_usados:
            continue

        codigos_usados.add(codigo)
        itens.append(item)

    return itens


def gerar_planilha_cotacao(itens):
    nome_arquivo = (
        "cotacao_" +
        datetime.now().strftime("%Y%m%d_%H%M%S") +
        ".xlsx"
    )

    caminho_saida = os.path.join(EXPORT_FOLDER, nome_arquivo)

    if not os.path.exists(MODELO_COTACAO):
        raise Exception("modelo_cotacao.xlsx nao encontrado")

    shutil.copy(MODELO_COTACAO, caminho_saida)

    wb = load_workbook(caminho_saida)
    ws = wb.active

    linha_inicio = 11
    coluna_codigo = 2
    coluna_qtd = 3
    coluna_descricao = 4

    areas_mescladas = list(ws.merged_cells.ranges)

    for area in areas_mescladas:
        min_col, min_row, max_col, max_row = area.bounds

        toca_area = not (
            max_row < linha_inicio or
            min_row > 80 or
            max_col < coluna_codigo or
            min_col > coluna_descricao
        )

        if toca_area:
            ws.unmerge_cells(str(area))

    for index, item in enumerate(itens):
        linha = linha_inicio + index

        ws.cell(row=linha, column=coluna_codigo).value = item.get("codigo", "")
        ws.cell(row=linha, column=coluna_qtd).value = item.get("qtd", "")
        ws.cell(row=linha, column=coluna_descricao).value = item.get("descricao", "")

    wb.save(caminho_saida)

    return nome_arquivo


@app.route("/", methods=["GET", "POST"])
def home():
    if not usuario_logado():
        return redirect("/login")

    nome = session["usuario_nome"]
    resposta_agente = None
    historico = buscar_historico(nome)

    if request.method == "POST":
        mensagem = request.form["mensagem"]

        resposta_agente = responder_chat(
            mensagem,
            historico
        )

        salvar_mensagem(
            nome,
            mensagem,
            resposta_agente
        )

        historico = buscar_historico(nome)

    return render_template(
        "index.html",
        nome=nome,
        resposta_agente=resposta_agente,
        historico=historico
    )


@app.route("/cotacao", methods=["GET", "POST"])
def cotacao():
    if not usuario_logado():
        return redirect("/login")

    erro = None
    arquivo = None
    texto_ocr = None

    if request.method == "POST":
        arquivo_enviado = request.files.get("imagem")

        if not arquivo_enviado:
            erro = "Nenhum arquivo enviado"

        else:
            nome_seguro = secure_filename(arquivo_enviado.filename)

            caminho_arquivo = os.path.join(
                UPLOAD_FOLDER,
                nome_seguro
            )

            arquivo_enviado.save(caminho_arquivo)

            try:
                texto_ocr = ler_texto_arquivo(caminho_arquivo)

                itens = extrair_itens_do_texto(texto_ocr)

                if not itens:
                    erro = "Nao consegui encontrar itens"

                else:
                    arquivo = gerar_planilha_cotacao(itens)

            except Exception as e:
                erro = str(e)
print("=" * 50)
print("TEXTO OCR:")
print(texto_ocr)
print("=" * 50)
    return render_template(
        "cotacao.html",
        erro=erro,
        arquivo=arquivo,
        texto_ocr=texto_ocr,
        nome=session.get("usuario_nome")
    )


@app.route("/download/<nome_arquivo>")
def download(nome_arquivo):
    caminho = os.path.join(EXPORT_FOLDER, nome_arquivo)

    return send_file(
        caminho,
        as_attachment=True
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None

    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        usuario = buscar_usuario(nome)

        if usuario and check_password_hash(
            usuario["senha"],
            senha
        ):
            session["usuario_nome"] = nome
            return redirect("/")

        erro = "Nome ou senha incorretos"

    return render_template(
        "login.html",
        erro=erro
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    erro = None

    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        if buscar_usuario(nome):
            erro = "Esse nome ja existe"

        else:
            criar_usuario(nome, senha)
            session["usuario_nome"] = nome
            return redirect("/")

    return render_template(
        "register.html",
        erro=erro
    )


@app.route("/logout")
def logout():
    session.clear()

    return redirect("/login")


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )