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

MODELO_COTACAO = os.path.join(
    TEMPLATE_FOLDER,
    "modelo_cotacao.xlsx"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)


def usuario_logado():
    return "usuario_nome" in session


def buscar_usuario(nome):
    resposta = (
        supabase
        .table("usuarios")
        .select("*")
        .eq("nome", nome)
        .execute()
    )

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
    textos = []

    imagem = preparar_imagem_para_ocr(caminho_imagem)

    configs = [
        "--psm 6",
        "--psm 4",
        "--psm 11"
    ]

    for config in configs:
        try:
            texto = pytesseract.image_to_string(
                imagem,
                lang="eng",
                config=config
            )
            textos.append(texto)
        except:
            pass

    melhor_texto = ""
    melhor_score = -1

    for texto in textos:
        score = pontuar_texto_ocr(texto)

        if score > melhor_score:
            melhor_score = score
            melhor_texto = texto

    return melhor_texto


def pontuar_texto_ocr(texto):
    if not texto:
        return 0

    texto_upper = texto.upper()

    score = 0

    palavras_boas = [
        "FILTRO",
        "ELEMENTO",
        "DONALDSON",
        "MANN",
        "WEGA",
        "RACOR",
        "CATERPILLAR",
        "VOLVO",
        "MOTOR",
        "OLEO",
        "AR",
        "COMBUSTIVEL",
        "HIDRAULICO",
        "QUANTIDADE",
        "MATERIAL",
        "PRODUTO",
        "DESCRICAO"
    ]

    for palavra in palavras_boas:
        if palavra in texto_upper:
            score += 10

    codigos = re.findall(
        r"\b[A-Z0-9\-]{4,20}\b",
        texto_upper
    )

    score += len(codigos) * 2
    score += len(texto) / 100

    return score


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

    codigo = re.sub(
        r"[^A-Z0-9\-]",
        "",
        codigo
    )

    return codigo


def linha_de_cabecalho(linha):
    linha = linha.upper()

    palavras = [
        "CODIGO",
        "COD",
        "COD FABRICAN",
        "FABRICAN",
        "PRODUTO",
        "QUANTIDADE",
        "QTD",
        "DESCRICAO",
        "DESCRICAO",
        "MATERIAL"
    ]

    total = 0

    for palavra in palavras:
        if palavra in linha:
            total += 1

    return total >= 2


def linha_de_lixo(linha):
    linha = linha.upper()

    ignorar = [
        "SOLICITACAO",
        "REQUISICAO",
        "NUMERO",
        "SOLICITANTE",
        "DATA",
        "EMISSAO",
        "FILIAL",
        "FORNECEDOR",
        "APLICACAO",
        "OBSERVACAO",
        "FUNCIONARIO",
        "APROVACAO",
        "TOTAL",
        "ENDERECO",
        "AVENIDA",
        "TELEFONE",
        "FOLHA",
        "USUARIO",
        "COMPRADOR"
    ]

    for palavra in ignorar:
        if palavra in linha:
            return True

    return False


def extrair_quantidade_inteligente(linha, numero_item_esperado):
    candidatos = re.findall(
        r"\b\d{1,4}(?:,\d{1,2})?\b",
        linha
    )

    quantidades = []

    for candidato in candidatos:
        normalizado = candidato.replace(",", ".")

        try:
            valor = float(normalizado)
        except:
            continue

        numero_item_texto = str(numero_item_esperado)
        numero_item_zero = str(numero_item_esperado).zfill(4)

        if candidato == numero_item_texto:
            continue

        if candidato == numero_item_zero:
            continue

        if valor < 1:
            continue

        if valor > 99:
            continue

        quantidades.append(candidato)

    if quantidades:
        return quantidades[0]

    return "1"


def extrair_descricao(linha, codigo, qtd):
    texto = linha.upper()

    texto = texto.replace(codigo, "")
    texto = texto.replace(str(qtd), "")

    texto = re.sub(r"\b\d{5,20}\b", "", texto)
    texto = re.sub(r"\b[A-Z0-9\-]{5,20}\b", "", texto)

    texto = texto.replace("|", " ")
    texto = texto.replace("_", " ")
    texto = texto.replace("-", " ")

    texto = re.sub(r"[^A-Z ]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    if "LUB" in texto or "OLEO" in texto:
        return "FILTRO LUBRIFICANTE"

    if "COMB" in texto:
        return "FILTRO COMBUSTIVEL"

    if "AGUA" in texto:
        return "FILTRO SEPARADOR DE AGUA"

    if "CABINE" in texto:
        return "FILTRO AR CABINE"

    if "PRIM" in texto and "AR" in texto:
        return "FILTRO AR PRIMARIO"

    if "SEC" in texto and "AR" in texto:
        return "FILTRO AR SECUNDARIO"

    if "HIDRAUL" in texto:
        return "FILTRO HIDRAULICO"

    if "AR" in texto:
        return "FILTRO AR"

    if "ELEMENTO" in texto:
        return "ELEMENTO FILTRO"

    if "FILTRO" in texto:
        return "FILTRO"

    if texto:
        return texto

    return "FILTRO"


def codigo_valido(codigo):
    codigo = limpar_codigo(codigo)

    if len(codigo) < 4:
        return False

    if not re.search(r"\d", codigo):
        return False

    if codigo.isdigit() and len(codigo) <= 3:
        return False

    ruins = [
        "DE1",
        "2026",
        "006",
        "18775",
        "005922",
        "0001",
        "0002",
        "0003",
        "0004",
        "0005",
        "0006",
        "0007",
        "0008",
        "0009",
        "0010",
        "0011",
        "0012"
    ]

    if codigo in ruins:
        return False

    return True


def extrair_por_linha_livre(texto):
    itens = []
    linhas = texto.splitlines()
    codigos_usados = set()
    numero_item_esperado = 1

    for linha in linhas:
        linha_original = linha
        linha = linha.strip().upper()

        if not linha:
            continue

        if linha_de_lixo(linha):
            continue

        if linha_de_cabecalho(linha):
            continue

        codigos = re.findall(
            r"\b[A-Z0-9\-]{4,20}\b",
            linha
        )

        if not codigos:
            continue

        codigo = None

        for candidato in codigos:
            candidato = limpar_codigo(candidato)

            if not codigo_valido(candidato):
                continue

            if candidato in codigos_usados:
                continue

            codigo = candidato
            break

        if not codigo:
            continue

        duplicado = False

        for usado in codigos_usados:
            if codigo in usado or usado in codigo:
                duplicado = True

        if duplicado:
            continue

        qtd = extrair_quantidade_inteligente(
            linha_original,
            numero_item_esperado
        )

        descricao = extrair_descricao(
            linha_original,
            codigo,
            qtd
        )

        codigos_usados.add(codigo)

        itens.append({
            "codigo": codigo,
            "qtd": qtd,
            "descricao": descricao
        })

        numero_item_esperado += 1

    return itens


def extrair_por_tabela(texto):
    itens = []
    linhas = texto.splitlines()

    inicio_tabela = None

    for i, linha in enumerate(linhas):
        if linha_de_cabecalho(linha):
            inicio_tabela = i + 1
            break

    if inicio_tabela is None:
        return []

    codigos_usados = set()
    numero_item_esperado = 1

    for linha in linhas[inicio_tabela:]:
        linha_original = linha
        linha = linha.strip().upper()

        if not linha:
            continue

        if linha_de_lixo(linha):
            continue

        if linha_de_cabecalho(linha):
            continue

        codigos = re.findall(
            r"\b[A-Z0-9\-]{4,20}\b",
            linha
        )

        if not codigos:
            continue

        codigo = None

        for candidato in codigos:
            candidato = limpar_codigo(candidato)

            if not codigo_valido(candidato):
                continue

            if candidato in codigos_usados:
                continue

            if "PE" in candidato and len(candidato) > 8:
                continue

            codigo = candidato
            break

        if not codigo:
            continue

        duplicado = False

        for usado in codigos_usados:
            if codigo in usado or usado in codigo:
                duplicado = True

        if duplicado:
            continue

        qtd = extrair_quantidade_inteligente(
            linha_original,
            numero_item_esperado
        )

        descricao = extrair_descricao(
            linha_original,
            codigo,
            qtd
        )

        codigos_usados.add(codigo)

        itens.append({
            "codigo": codigo,
            "qtd": qtd,
            "descricao": descricao
        })

        numero_item_esperado += 1

    return itens


def pontuar_itens(itens):
    if not itens:
        return 0

    score = 0

    score += len(itens) * 10

    for item in itens:
        codigo = item.get("codigo", "")
        qtd = item.get("qtd", "")
        descricao = item.get("descricao", "")

        if codigo:
            score += 5

        if qtd:
            score += 3

        if descricao and descricao != "FILTRO":
            score += 4

        if len(codigo) >= 5:
            score += 2

    return score


def escolher_melhor_resultado(itens_tabela, itens_livre):
    score_tabela = pontuar_itens(itens_tabela)
    score_livre = pontuar_itens(itens_livre)

    if score_tabela >= score_livre:
        return itens_tabela

    return itens_livre


def extrair_cotacao_por_regex(texto):
    itens_tabela = extrair_por_tabela(texto)
    itens_livre = extrair_por_linha_livre(texto)

    return escolher_melhor_resultado(
        itens_tabela,
        itens_livre
    )


def gerar_planilha_cotacao(itens):
    nome_arquivo = (
        "cotacao_" +
        datetime.now().strftime("%Y%m%d_%H%M%S") +
        ".xlsx"
    )

    caminho_saida = os.path.join(
        EXPORT_FOLDER,
        nome_arquivo
    )

    if not os.path.exists(MODELO_COTACAO):
        raise Exception("modelo_cotacao.xlsx nao encontrado")

    shutil.copy(
        MODELO_COTACAO,
        caminho_saida
    )

    wb = load_workbook(caminho_saida)
    ws = wb.active

    linha_inicio = 11
    coluna_codigo = 2
    coluna_qtd = 3
    coluna_descricao = 4

    for index, item in enumerate(itens):
        linha = linha_inicio + index

        ws.cell(
            row=linha,
            column=coluna_codigo
        ).value = item.get("codigo", "")

        ws.cell(
            row=linha,
            column=coluna_qtd
        ).value = item.get("qtd", "")

        ws.cell(
            row=linha,
            column=coluna_descricao
        ).value = item.get("descricao", "")

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
            nome_seguro = secure_filename(
                arquivo_enviado.filename
            )

            caminho_arquivo = os.path.join(
                UPLOAD_FOLDER,
                nome_seguro
            )

            arquivo_enviado.save(caminho_arquivo)

            try:
                texto_ocr = ler_texto_arquivo(
                    caminho_arquivo
                )

                itens = extrair_cotacao_por_regex(
                    texto_ocr
                )

                if not itens:
                    erro = "Nao consegui encontrar itens"

                else:
                    arquivo = gerar_planilha_cotacao(
                        itens
                    )

            except Exception as e:
                erro = str(e)

    return render_template(
        "cotacao.html",
        erro=erro,
        arquivo=arquivo,
        texto_ocr=texto_ocr
    )


@app.route("/download/<nome_arquivo>")
def download(nome_arquivo):
    caminho = os.path.join(
        EXPORT_FOLDER,
        nome_arquivo
    )

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