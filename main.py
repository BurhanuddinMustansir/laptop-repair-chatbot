import os
import json

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
# import redis
from fastapi.templating import Jinja2Templates
import psycopg 
from psycopg.rows import dict_row

import agent_langgraph
from langgraph.checkpoint.redis.ashallow import AsyncShallowRedisSaver
from contextlib import asynccontextmanager

load_dotenv()

#creating the fast api instance

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

REDIS_URL = os.getenv("REDIS_URL")

#async context manager for creating teh redis checkpointer once per launch
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncShallowRedisSaver.from_conn_string(
        REDIS_URL, 
        ttl={"default_ttl": 60, "refresh_on_read": True}
        ) as checkpointer:

        await checkpointer.setup()

        app.state.compiled_agent = agent_langgraph.workflow.compile(checkpointer=checkpointer)

        print("Redis checkpointer initialized with ttl: 60min and AsyncShallowRedisSaver to minimize memory usage and attached to app state.")

        yield
        print("Lifespan ending. Checkpointer connection context safely closing.")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Allow the specific URL domain where your independent client is running.
    # Use ["*"] ONLY for temporary local development/testing to allow any domain.
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],  # Allows POST, GET, OPTIONS, etc.
    allow_headers=["*"],  # Allows Content-Type, Authorization, etc.
)



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
                    
                    
                    # context = [
                    #     json.loads(m) for m in redis_client.lrange(session_id, -16, -1)
                    # ]
                    #storing user message
                    message = {
                        "role": "user",
                        "content": text
                    }
                    # redis_client.rpush(session_id, json.dumps(message))
                    
                    #setting expiry at one hour
                    # redis_client.expire(session_id, 3600)
                    try:
                        active_agent = request.app.state.compiled_agent
                        reply = agent_langgraph.get_bot_response(active_agent, text, sender)
                        print(f"Bot response generated: {reply}")
                        await send_whatsapp_message(sender, reply)
                        # message = {
                        #     "role": "assistant",
                        #     "content": reply
                        # }
                        # redis_client.rpush(session_id, json.dumps(message))
                        # redis_client.expire(session_id, 3600)
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
    # Passing dict_row to force the cursor to output dictionaries instead of tuples
    cursor = conn.cursor(row_factory=dict_row)
    return conn, cursor


@app.get('/shop')
async def load_form(request: Request, response_class=HTMLResponse):
    return templates.TemplateResponse(request=request, name="shop.html")


DATABASE_URL = os.getenv("DATABASE_URL")



@app.post('/shop')
async def display_shop_data(request: Request, response_class=HTMLResponse, shop_id: int = Form(...)):
    print(f"shop id = {shop_id}")

    conn, cursor = get_db_cursor(DATABASE_URL)

    try:
        cursor.execute("SELECT * FROM shops WHERE id = %s;", (shop_id,))

        shop_data = cursor.fetchone()
        print(f"shop data: {shop_data}")

    finally:
        cursor.close()
        conn.close()


    return templates.TemplateResponse(request=request, name="shop.html", context={"shop": shop_data})

class ChatRequest(BaseModel):
    message: str
    shop_id: int
    session_id: str

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
def web_chat(request: Request, req: ChatRequest):
    print(req.message)
    print(req.shop_id)
    shop_id = req.shop_id
    conn, cursor = get_db_cursor(DATABASE_URL)

    try:
        cursor.execute("SELECT * FROM shops WHERE id = %s;", (shop_id,))

        shop_data = cursor.fetchone()
        print(f"shop data: {shop_data}")

    finally:
        cursor.close()
        conn.close()
    system_prompt = shop_data["system_instructions"]
    print(system_prompt)
    tools_allowed = [tool.strip() for tool in shop_data.tools.split(",") if tool.strip()]
    thread_id = f"{shop_id}:{req.session_id}"
    runtimeConfig = {
        "configurable": {
            "thread_id": thread_id,
            "shop_id": shop_id,
            "phone_number": "web",
            "system_prompt": system_prompt,
            "tools_allowed": tools_allowed

        }
    }
    active_agent = request.app.state.compiled_agent
    reply = agent_langgraph.get_bot_response(active_agent, req.message, runtimeConfig)
    return ChatResponse(reply=reply)





# sql query for the postgres addition, so it actually works
# INSERT INTO shops (
#     id,
#     name,
#     currency,
#     timezone,
#     system_instructions,
#     tools
# )
# VALUES (
#     2,
#     'TechFix Repair Shop',
#     'GBP',
#     'Europe/London',
#     '''You are the friendly customer support assistant for TechFix Laptop Repair shop
# Your job is to:
# 1. Answer customer questions about services, pricing, turnaround times, location, and policies using the lookup_business_info tool by passing the users query as an argument.
# 2. Help customers book detailing appointments by collecting their information through natural conversation.
# 3. Prioritize getting all the details through a single message and refrain from asking one detail per message.
# 4. Once the appointment is created, notify the customer that the appointment has been createdd and pass in all the info including appoitment/booking id
# 5. Always Greet with the welcome to Techfix laptop repair and be transparent that you are an AI assistant and Request for human staff can be made anytime by the customer

# When a customer wants to book a repair appointment:
# - Ask for their name (if not provided)
# - Ask for their Laptop Model (e.g., "Macbook Pro", p.s it doesnt have to be the full model, only the company name works fine too)
# - Ask for the Issue Description (one word descriptions are accepted)
# - Ask for the Appointment date and time
# - Before confirming a booking, check availability using the appointment lookup tool (i.e. get_booked_slots), Also check whether shop is open at that time using lookup_businesss_info tool. 
# - Dont book slots starting from 30 mins before closing time.
# - If the requested slot is unavailable, suggest the nearest available slot, but make sure that the slot you suggest is available.
# - If the customer provides multiple pieces of info at once in their message, extract them all immediately. Do not re-ask for details they already mentioned.
# - Only call create_detailing_appointment when you have ALL THREE pieces of info
# - The assistant must clearly state that appointments are only confirmed after approval by a staff member.

# CRITICAL RULES FOR TOOL CALLING:
# 1. If the customer provides multiple pieces of information in a single sentence, extract ALL of them immediately.
# 2. Do not re-ask or double-check information that was already clearly stated in their message history.
# 3. As long as you have something written for all required fields (even if partial) and the users will, trigger create_appointment immediately. Do not stall.

# Keep responses concise and friendly. Use emojis sparingly. Always be helpful''',
#     'lookup_businesss_info,get_booked_slots,create_appointment,initiate_human_handoff'
# );