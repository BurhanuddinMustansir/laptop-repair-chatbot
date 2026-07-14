import os
import json

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import redis
from fastapi.templating import Jinja2Templates
import psycopg 
from psycopg.rows import dict_row

from agent import get_bot_response

load_dotenv()

#creating the fast api instance
app = FastAPI(title="TechFix WhatsApp Bot")

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

redis_client = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

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

    print("INCOMING WEBHOOK PAYLOAD:", json.dumps(body, indent=2))

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
                    # generating session id for cach in database
                    session_id = f"session:{sender}"
                    
                    
                    context = [
                        json.loads(m) for m in redis_client.lrange(session_id, -16, -1)
                    ]
                    #storing user message
                    message = {
                        "role": "user",
                        "content": text
                    }
                    redis_client.rpush(session_id, json.dumps(message))
                    
                    #setting expiry at one hour
                    redis_client.expire(session_id, 3600)
                    try:
                        reply = get_bot_response(text, sender, context)
                        print(f"Bot response generated: {reply}")
                        await send_whatsapp_message(sender, reply)
                        message = {
                            "role": "assistant",
                            "content": reply
                        }
                        redis_client.rpush(session_id, json.dumps(message))
                        redis_client.expire(session_id, 3600)
                    except Exception as e:
                        print(f"Error executing bot or sending message: {e}")

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
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text}
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        # CRITICAL DEBUG LINE: Check Meta's delivery server status output
        print(f"Meta Response Status: {response.status_code}")
        print(f"Meta Response Body: {response.text}")



class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
def web_chat(req: ChatRequest):
    reply = get_bot_response(req.message)
    return ChatResponse(reply=reply)


@app.post("/update")
async def recieve_update(request: Request):
    body = await request.json()
    
    print(f"INCOMING UPDATE REQUEST: {json.dumps(body, indent=2)}")

    phone = body.get("phone_number")
    name = body.get("customer_name")
    appointment_id = body.get("appointment_id")

    message = f"{name} Your appointment with the Appointment-ID: {appointment_id} has been confirmed"
    try: 
        await send_whatsapp_message(phone, message)
    except Exception as e:
        print(f"Error sending whatsapp message: {e}")

    return {"status": "ok"}


templates = Jinja2Templates(directory="templates")



def get_db_cursor(connection_string):
    conn = psycopg.connect(connection_string)
    # Passing dict_row forces the cursor to output dictionaries instead of tuples
    cursor = conn.cursor(row_factory=dict_row)
    return conn, cursor


@app.get('/shop')
async def load_form(request: Request, response_class=HTMLResponse):
    return templates.TemplateResponse(request=request, name="shop.html")


DATABASE_URL = os.getenv("DATABASE_URL")



@app.post('/shop')
async def display_shop_data(request: Request, response_class=HTMLResponse):
    body = request.json()
    shop_id = body.get("shop_id")

    conn, cursor = get_db_cursor(DATABASE_URL)

    try:
        cursor.execute("SELECT * FROM shops WHERE id = %s;", (shop_id,))

        shop_data = cursor.fetchone()

    finally:
        cursor.close()
        conn.close()


    return templates.TemplateResponse(request=request, name="shop.html", context={"shop": shop_data})
