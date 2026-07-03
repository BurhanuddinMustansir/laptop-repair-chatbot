import csv
import os
from datetime import datetime
from pathlib import Path 

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime

from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import Annotated
from langchain_core.tools.base import InjectedToolArg
from langchain_core.runnables import RunnableConfig


load_dotenv()

KNOWLEDGE_BASE_PATH = Path(__file__).parent/ "knowledge_base.txt"
ORDERS_CSV_PATH = Path(__file__).parent / "orders.csv"

def load_knowledge_base() -> str:
    return KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")



@tool
def lookup_services(query: str) -> str:
    """Look up information about TechFix repair services, pricing, hours, policies, and general business info. 
    Use this tool when the customer asks about services, pricing, turnaround times, location, hours, or policies."""
    # Return the full knowledge base for the LLM to extract relevant info
    knowledge = load_knowledge_base()
    return knowledge

@tool 
def create_repair_order(customer_name: str, device: str, issue_description: str, config: RunnableConfig = None) -> str:
    """Create a new repair order. Use this tool ONLY when you have collected ALL THREE pieces of information from 
    the customer: their name, device model, and issue description. Do NOT ask for their contact phone number, as it is handled automatically.
    
    Returns:
        A success message with the Order ID, or an error message.
        
    Error Handling Instructions for the Agent:
        If this tool returns an error or indicates the API is down, 
        apologize sincerely to the user, inform them that orders cannot 
        be processed automatically right now, and let them know you are 
        escalating their query to a manual human operator. Do not retry 
        the tool immediately.
    """
    contact_number = ""
    if config and "configurable" in config:
        contact_number = config["configurable"].get("contact_number", "")
        
    print(f"🤖 MANUAL INJECTION DEBUG: Extracted phone is {contact_number}")
    
    if not contact_number:
        return "Error: Internal mapping exception, phone number missing."


    credentials_info = {
        "type": "service_account",
        "project_id": os.getenv("GCP_PROJECT_ID"),
        "private_key_id": os.getenv("GCP_PRIVATE_KEy_ID"),
        # fixing potential newline formatting issues
        "private_key": os.getenv("GCP_PRIVATE_KEY").replace('\\n', '\n'),
        "client_email": os.getenv("GCP_CLIENT_EMAIL"),
        "client_id": os.getenv("GCP_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/laptop-repair-server%40laptop-repair-2008.iam.gserviceaccount.com",
        "universe_domain": "googleapis.com"
    }

    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

    creds = service_account.Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
    order_id = f"Order-ID-{datetime.now().strftime("%Y%m%d%H%M")}"
    try:
        service = build("sheets", "v4", credentials=creds)

        values = [
            [customer_name, contact_number, device, f"{datetime.now().strftime("%Y-%m-%d %H:%M")}", "Pending", issue_description, order_id]
        ]

        body = {"values": values}
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=SPREADSHEET_ID,
                range='A1',
                valueInputOption="USER_ENTERED",
                body=body,
                insertDataOption="INSERT_ROWS"
            )
            .execute()
        )
        rows = f"{(result.get('updates').get('updatedCells'))} cells appended."
        print(rows)
        return f"Success: Order Created with order ID = {order_id} "

    except HttpError as error:
        return {"error: ", error}
    
SYSTEM_PROMPT = """You are the friendly customer support assistant for TechFix 
Laptop Repair shop.
Your job is to:
1. Answer customer questions about services, pricing, turnaround times, location, and policies using the lookup_services tool.
2. Help customers place repair orders by collecting their information through natural conversation.

When a customer wants to book a repair:
- Ask for their name (if not provided)
- Ask for their device model (e.g., "Dell XPS 15", "MacBook Pro 2021" p.s it doesnt have to be the full model, only the company name works fine too)
- Ask for a description of the issue (accept short symptoms like "cracked screen" or "won't turn on" as a valid description)
- If the customer provides multiple pieces of info at once in their message, extract them all immediately. Do not re-ask for details they already mentioned.
- Only call create_repair_order when you have ALL THREE pieces of info

CRITICAL RULES FOR TOOL CALLING:
1. If the customer provides multiple pieces of information in a single sentence, extract ALL of them immediately.
2. Do not re-ask or double-check information that was already clearly stated in their message history.
3. As long as you have something written for all THREE fields (even if partial), trigger 'create_repair_order' immediately. Do not stall.

Keep responses concise and friendly. Use emojis sparingly. Always be helpful."""

def build_agent() -> AgentExecutor:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    tools = [lookup_services, create_repair_order]
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT), 
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"), 
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)

agent_executor = build_agent()

def get_bot_response(user_message: str, user_phone: str, chat_history: list) -> str:
    result = agent_executor.invoke(
        {
            "input": user_message,
            "chat_history": chat_history,
        },
        config={
            "configurable": {
                # This key name MUST match the variable name in the tool create_repair_order
                "contact_number": user_phone
            }
        }
    )
    return result["output"]