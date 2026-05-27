import os
import uuid

from flask import Flask
from flask import render_template
from flask import request
from flask import session

from openai import OpenAI

from agent.supabase_client import supabase


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)


app = Flask(__name__)

app.secret_key = "agent-secret-key"


def obter_usuario():

    if "user_id" not in session:

        session["user_id"] = str(uuid.uuid4())

    return session["user_id"]


def buscar_historico(user_id, limite=20):

    resposta = (
        supabase
        .table("tickets")
        .select("*")
        .eq("user_id", user_id)
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

    return resposta.choices[0].message.content.strip()


def salvar_mensagem(user_id, usuario, resposta):

    supabase.table("tickets").insert({

        "user_id": user_id,

        "ticket": usuario,

        "categoria": resposta

    }).execute()


@app.route("/", methods=["GET", "POST"])
def home():

    resposta_agente = None

    user_id = obter_usuario()

    historico = buscar_historico(user_id)

    if request.method == "POST":

        mensagem = request.form["mensagem"]

        resposta_agente = responder_chat(
            mensagem,
            historico
        )

        salvar_mensagem(
            user_id,
            mensagem,
            resposta_agente
        )

        historico = buscar_historico(user_id)

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