import os

from flask import Flask
from flask import render_template
from flask import request

from openai import OpenAI

from agent.supabase_client import supabase


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)


app = Flask(__name__)


def buscar_historico(limite=10):

    resposta = (
        supabase
        .table("tickets")
        .select("*")
        .order("id", desc=False)
        .limit(limite)
        .execute()
    )

    return resposta.data


def responder_chat(mensagem):

    historico = buscar_historico()

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

    return resposta.choices[0].message.content.strip()


def salvar_mensagem(usuario, resposta):

    supabase.table("tickets").insert({

        "ticket": usuario,

        "categoria": resposta

    }).execute()


@app.route("/", methods=["GET", "POST"])
def home():

    resposta_agente = None

    if request.method == "POST":

        mensagem = request.form["mensagem"]

        resposta_agente = responder_chat(mensagem)

        salvar_mensagem(mensagem, resposta_agente)

    historico = buscar_historico()

    return render_template(

        "index.html",

        resposta_agente=resposta_agente,

        historico=historico

    )


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )