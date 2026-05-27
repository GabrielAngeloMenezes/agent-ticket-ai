from openai import OpenAI
from agent.supabase_client import supabase

client = OpenAI()


class TicketAgent:

    def __init__(self):
        self.valid_categories = ["tecnico", "financeiro", "comercial", "geral"]

    def think(self, ticket):
        print("IA analisando ticket...")

        prompt = f"""
Classifique o ticket abaixo em apenas uma categoria:
- tecnico
- financeiro
- comercial
- geral

Ticket: {ticket}

Responda somente com o nome da categoria.
"""

        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt
        )

        category = response.output_text.strip().lower()

        if category not in self.valid_categories:
            return "geral"

        return category

    def save_memory(self, ticket, category):
        data = {
            "ticket": ticket,
            "categoria": category
        }

        supabase.table("tickets").insert(data).execute()

        print("")
        print("Salvo na nuvem com sucesso!")

    def show_memory(self):
        response = supabase.table("tickets").select("*").execute()
        return response.data