import os
import json

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import get_bot_response

load_dotenv()

#creating the fast api instance
app = FastAPI(title="TechFix WhatsApp Bot")

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/webhook")
def verify_webhook(request: Request):
    #whatsapp query parameters for verification
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)



@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.json()

    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}
    
    entries = body.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            for message in messages:
                if message.get("type") == "text":
                    sender = message.get("from")
                    text_object = message.get("text", {})
                    text = text_object.get("body")

                    reply = get_bot_response(text)
                    await send_whatsapp_message(sender, reply)

    return {"status": "ok"}

async def send_whatsapp_message(to: str, text:str):
    #whatsapp clound api for sending messages

    url= f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "indivisual",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=payload)



class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
def web_chat(req: ChatRequest):
    reply = get_bot_response(req.message)
    return ChatResponse(reply=reply)

