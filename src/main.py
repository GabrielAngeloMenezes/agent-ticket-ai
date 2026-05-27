import os

from flask import Flask, render_template, request, redirect, session
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash

from agent.supabase_client import supabase


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "agent-secret-key")


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
        mensagens.append({
            "role": "user",
            "content": item["ticket"]
        })

        mensagens.append({
            "role": "assistant",
            "content": item["categoria"]
        })

    mensagens.append({
        "role": "user",
        "content": mensagem
    })

    resposta = client.chat.completions.create(
        model="openrouter/free",
        messages=mensagens,
        temperature=0.7,
        max_tokens=300
    )

    conteudo = resposta.choices[0].message.content

    if conteudo is None or conteudo == "":
        return "Nao consegui gerar uma resposta agora. Tente novamente."

    return conteudo.strip()


def salvar_mensagem(nome, usuario, resposta):
    supabase.table("tickets").insert({
        "user_id": nome,
        "ticket": usuario,
        "categoria": resposta
    }).execute()


@app.route("/", methods=["GET", "POST"])
def home():
    if not usuario_logado():
        return redirect("/login")

    nome = session["usuario_nome"]
    resposta_agente = None
    historico = buscar_historico(nome)

    if request.method == "POST":
        mensagem = request.form["mensagem"]
        resposta_agente = responder_chat(mensagem, historico)
        salvar_mensagem(nome, mensagem, resposta_agente)
        historico = buscar_historico(nome)

    return render_template(
        "index.html",
        nome=nome,
        resposta_agente=resposta_agente,
        historico=historico
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None

    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        usuario = buscar_usuario(nome)

        if usuario and check_password_hash(usuario["senha"], senha):
            session["usuario_nome"] = nome
            return redirect("/")

        erro = "Nome ou senha incorretos."

    return render_template("login.html", erro=erro)


@app.route("/register", methods=["GET", "POST"])
def register():
    erro = None

    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        if buscar_usuario(nome):
            erro = "Esse nome ja existe."
        else:
            criar_usuario(nome, senha)
            session["usuario_nome"] = nome
            return redirect("/")

    return render_template("register.html", erro=erro)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )