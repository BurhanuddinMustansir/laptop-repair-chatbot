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
from zoneinfo import ZoneInfo
from openai import OpenAI
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery

load_dotenv()

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


KNOWLEDGE_BASE_PATH = Path(__file__).parent/ "knowledge_base.txt"

def load_knowledge_base() -> str:
    return KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")



@tool
def lookup_businesss_info(query: str) -> str:
    """
    Look up information about TechFix's services, pricing, business hours, repair turnaround times, location, and company policies.

    Use this tool whenever the customer asks a question that requires knowledge about the business.

    Pass the user's current query as the argument. The tool will perform semantic search over the knowledge base and return only the most relevant context needed to answer the question.
    """

    def create_embeddings(text):
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
            encoding_format="float"
        )
        return response.data[0].embedding

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=OPENAI_API_KEY)
    index = SearchIndex.from_yaml("schema.yaml", redis_url=os.getenv("REDIS_URL"))

    query_embedding = create_embeddings(query)
    query = VectorQuery(
        vector=query_embedding,
        vector_field_name="embedding",
        return_fields=["text"],
        num_results=1
    )

    result = index.query(query)
    context = result[0]["text"]

    return context

@tool
def get_booked_slots(date):
    """
    Returns all booked appointment times for a given date.

    Use this tool whenever a customer wants to book or reschedule an appointment.

    Pass the appointment date as the argument in ISO format (YYYY-MM-DD).

    The tool returns a list of booked time slots for that date.

    Before confirming an appointment, check whether the user's requested time already exists in the returned list.
    If it does, inform the customer that the slot is unavailable and suggest the nearest available time.
    If it does not, proceed with creating the appointment.
    """

    try:
        service = build("sheets", "v4", credentials=creds)

        result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=SPREADSHEET_ID, range='H:I', majorDimension="ROWS")
                .execute()
                )
        
        date = datetime.strptime(date, "%Y-%m-%d")
        booked_slots = []

        for booking in result["values"][1:]:
            if not booking:
                continue
            booked_date = datetime.strptime(booking[0], "%Y-%m-%d")
            if booked_date == date:
                booked_slots.append(booking[1])

            
        return booked_slots

    except Exception as e:
        print(f"error in get_booked_slots {e}")
        return f"error: {e}"


#using toolfactory temporarily for making it work, should be shifted to langgraph later
def build_repair_order_tool(phone_number: str):

    @tool 
    def create_repair_appointment(customer_name: str, device: str, issue_description: str, appointment_date: str, appointment_time: str, contact_number: str = phone_number) -> str:
        """Create a new repair appoitment. Use this tool ONLY when you have collected ALL FIVE pieces of information from 
        the customer: their name, device model, issue description, Appointment date in ISO format (YYYY-MM-DD), and appointment time in the format (HH:MM). Do NOT ask for their contact phone number, as it is handled automatically.
        
        Returns:
            A success message with the appointment ID, or an error message.
            
        Error Handling Instructions for the Agent:
            If this tool returns an error or indicates the API is down, 
            apologize sincerely to the user, inform them that bookings/appointments cannot 
            be processed automatically right now, and let them know you are 
            escalating their query to a manual human operator. Do not retry 
            the tool immediately.
        """
        
        if not contact_number:
            return "Error: Phone number failed to pass through the executor chain."


        
        appointment_id = f"{datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y%m%d%H%M")}"
        appointment_date = datetime.strptime(appointment_date, "%Y-%m-%d").strftime("%Y-%m-%d")
        appointment_time = datetime.strptime(appointment_time, "%H:%M").strftime("%H:%M")
        created_at = datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d %H:%M")
        try:
            service = build("sheets", "v4", credentials=creds)

            values = [
                [customer_name, contact_number, device, created_at, "Pending", issue_description, appointment_id, appointment_date, appointment_time]
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
            return f"Success: Booking Created with appointment ID = {appointment_id} "

        except HttpError as error:
            print(f"error in create_repair_appointment: {error}")
            return {"error: ", error}
        
    return create_repair_appointment
    
    
SYSTEM_PROMPT = """You are the friendly customer support assistant for TechFix 
Laptop Repair shop.
Your job is to:
1. Answer customer questions about services, pricing, turnaround times, location, and policies using the lookup_business_info tool by passing the users query as an argument.
2. Help customers book repair appointments by collecting their information through natural conversation.
3. Prioritize getting all the details through a single message and refrain from asking one detail per message

When a customer wants to book a repair appointment:
- Ask for their name (if not provided)
- Ask for their device model (e.g., "Dell XPS 15", "MacBook Pro 2021" p.s it doesnt have to be the full model, only the company name works fine too)
- Ask for a description of the issue (accept short symptoms like "cracked screen" or "won't turn on" as a valid description)
- Ask for the Appointment date and time
- Before confirming a booking, check availability using the appointment lookup tool (i.e. get_booked_slots). 
- If the requested slot is unavailable, suggest the nearest available slot.
- If the customer provides multiple pieces of info at once in their message, extract them all immediately. Do not re-ask for details they already mentioned.
- Only call create_repair_appointment when you have ALL THREE pieces of info

CRITICAL RULES FOR TOOL CALLING:
1. If the customer provides multiple pieces of information in a single sentence, extract ALL of them immediately.
2. Do not re-ask or double-check information that was already clearly stated in their message history.
3. As long as you have something written for all THREE fields (even if partial), trigger 'create_repair_appointment' immediately. Do not stall.

Keep responses concise and friendly. Use emojis sparingly. Always be helpful."""

def build_agent(tools) -> AgentExecutor:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT), 
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"), 
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)



def get_bot_response(user_message: str, user_phone: str, chat_history: list) -> str:
    create_repair_appointment = build_repair_order_tool(phone_number=user_phone)
    tools = [lookup_businesss_info, create_repair_appointment, get_booked_slots]
    agent_executor = build_agent(tools)

    result = agent_executor.invoke(
        {
            "input": user_message,
            "chat_history": chat_history,
        }
    )
    return result["output"]