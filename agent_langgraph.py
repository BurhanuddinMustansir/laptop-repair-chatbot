from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated, List
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import ToolNode
from langchain_core.runnables import RunnableConfig
from operator import add
from langgraph.checkpoint.redis import RedisSaver
import os
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path 
from langgraph.graph.state import CompiledStateGraph

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime

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

# SERVICE_ACCOUNT_FILE = "service_account_credentials.json"
# creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE)
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")


KNOWLEDGE_BASE_PATH = Path(__file__).parent/"resources/car_detailer_knowledge_base.txt"

def load_knowledge_base() -> str:
    return KNOWLEDGE_BASE_PATH.read_text(encoding="utf-8")



#in a real environment, each convo should start with a conversations status set to AI, and this part should update it to a human
@tool
def initiate_human_handoff(config: RunnableConfig):
    """Use this tool when the user requests to chat with a human/staff"""
    try:
        created_at = f"{datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d %H:%M")}"

        configurable = config.get("configurable", {})
        contact_number = configurable.get("phone_number", None)

        service = build("sheets", "v4", credentials=creds)
        #getting all the previous customer numbers
        result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=SPREADSHEET_ID, range='Conversations!A:B', majorDimension="ROWS")
                .execute()
                )
        print(result)
        #checking whether this number has the status of AI
        for index, data in enumerate(result["values"][1:]):
            if not data:
                continue
            phone = data[0]
            print(phone)
            if phone == str(contact_number):
                target_range = f'Conversations!B{index+2}:C{index+2}'
                
                values = [["Human", created_at]]
                body = {"values": values}
                updateResult = (
                                service.spreadsheets()
                                .values()
                                .update(
                                    spreadsheetId=SPREADSHEET_ID,
                                    range=target_range,
                                    valueInputOption="USER_ENTERED",
                                    body=body,
                                )
                                .execute()
                            )
                return "human handoff request has been successfully initiated"
    
    except HttpError as e:
        return "error executing human handoff request: error"



@tool
def lookup_businesss_info(query: str) -> str:
    """
    Look up information about Spark auto detailing's services, pricing, business hours, turnaround times, location, and company policies.

    Use this tool whenever the customer asks a question that requires knowledge about the business.

    Pass the user's current query as the argument or create a relevent query. The tool will perform semantic search over the knowledge base and return only the most relevant context needed to answer the question.

    Use this tool also when booking an appointment to confirm whether the shop is open on the users preffered appointment date and time
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
    If it does, inform the customer that the slot is unavailable and suggest the nearest available time, but make sure that the nearest slot you suggest is not already booked too.
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

        print(booked_slots)

            
        return f"following slots are booked for {date}: {booked_slots}"

    except Exception as e:
        print(f"error in get_booked_slots {e}")
        return f"error: {e}"



@tool 
def create_detailing_appointment(customer_name: str, vehicle_make_model: str, service_required: str, appointment_date: str, appointment_time: str, config: RunnableConfig) -> str:
    """Create a new repair appoitment. Use this tool ONLY when you have collected ALL FIVE pieces of information from 
    the customer: their name, Vehicle make and model, service required, Appointment date in ISO format (YYYY-MM-DD), and appointment time in the format (HH:MM). Do NOT ask for their contact phone number, as it is handled automatically.
    
    Returns:
        A success message with the appointment ID, or an error message.
        
    Error Handling Instructions for the Agent:
        If this tool returns an error or indicates the API is down, 
        apologize sincerely to the user, inform them that bookings/appointments cannot 
        be processed automatically right now, and let them know you are 
        escalating their query to a manual human operator. Do not retry 
        the tool immediately.
    """
    
    configurable = config.get("configurable", {})
    contact_number = configurable.get("phone_number", None)

    if not contact_number:
        return "Error: Phone number failed to pass through the executor chain."


    
    appointment_id = f"{datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y%m%d%H%M")}"
    appointment_date = f"{datetime.strptime(appointment_date, "%Y-%m-%d").strftime("%Y-%m-%d")}"
    appointment_time = f"{datetime.strptime(appointment_time, "%H:%M").strftime("%H:%M")}"
    created_at = f"{datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d %H:%M")}"
    try:
        service = build("sheets", "v4", credentials=creds)

        values = [
            [customer_name, contact_number, vehicle_make_model, created_at, "Pending", service_required, appointment_id, appointment_date, appointment_time]
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
        print(f"error in create_detailing_appointment: {error}")
        return {"error: ", error}
        


today = datetime.now().strftime("%A, %B %d, %Y")
    
    
SYSTEM_PROMPT = f"""You are the friendly customer support assistant for Spark Auto Detailing
Your job is to:
1. Answer customer questions about services, pricing, turnaround times, location, and policies using the lookup_business_info tool by passing the users query as an argument.
2. Help customers book detailing appointments by collecting their information through natural conversation.
3. Prioritize getting all the details through a single message and refrain from asking one detail per message.
4. Once the appointment is created, notify the customer that the appointment has been createdd and pass in all the info including appoitment/booking id
5. Always Greet with the welcome to Spark Auto Detailing and be transparent that you are an AI assistant and Request for human staff can be made anytime by the customer

When a customer wants to book a repair appointment:
- Ask for their name (if not provided)
- Ask for their Vehicle Make and Model (e.g., "Porche 911", p.s it doesnt have to be the full model, only the company name works fine too)
- Ask for the Service Required
- Ask for the Appointment date and time
- Before confirming a booking, check availability using the appointment lookup tool (i.e. get_booked_slots), Also check whether shop is open at that time using lookup_businesss_info tool. 
- Dont book slots starting from 30 mins before closing time.
- If the requested slot is unavailable, suggest the nearest available slot, but make sure that the slot you suggest is available.
- If the customer provides multiple pieces of info at once in their message, extract them all immediately. Do not re-ask for details they already mentioned.
- Only call create_detailing_appointment when you have ALL THREE pieces of info
- The assistant must clearly state that appointments are only confirmed after approval by a staff member.

IMPORTANT INFO FOR BOOKING CONFLICT DETECTION AND USING get_booked_slots:
-Today's date {today}.
-When the user refers to relative dates such as today, tomorrow, this Friday, or next Tuesday, interpret them relative to today's date and convert them to ISO format (YYYY-MM-DD) before using any tools.

CRITICAL RULES FOR TOOL CALLING:
1. If the customer provides multiple pieces of information in a single sentence, extract ALL of them immediately.
2. Do not re-ask or double-check information that was already clearly stated in their message history.
3. As long as you have something written for all required fields (even if partial), trigger 'create_detailing_appointment' immediately. Do not stall.

Keep responses concise and friendly. Use emojis sparingly. Always be helpful."""


#this part is the new langgraph bot, everything above stays the same

#defining our state
class AgentState(TypedDict):
    messages: Annotated[List, add]


tools_list = [lookup_businesss_info, get_booked_slots, create_detailing_appointment, initiate_human_handoff]
tools_node = ToolNode(tools_list)

def tools_condition(state: AgentState):
    messages = state["messages"][-1]
    if messages.tool_calls:
        return "tool_node"
    else:
        return END
    
def route_query(state: AgentState, config: RunnableConfig):
    try:
        configurable = config.get("configurable", {})
        contact_number = configurable.get("phone_number", None)
        service = build("sheets", "v4", credentials=creds)
        #getting all the previous customer numbers
        result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=SPREADSHEET_ID, range='Conversations!A:B', majorDimension="ROWS")
                .execute()
                )
        print(result)
        #checking whether this number has the status of AI
        for index, data in enumerate(result["values"][1:]):
            if not data:
                continue
            phone = data[0]
            print(phone)
            if phone == str(contact_number):
                status = data[1]
                if status == "Human":
                    return END
                elif status == "AI":
                    return "agent_node"
                
        created_at = f"{datetime.now(ZoneInfo("Asia/Karachi")).strftime("%Y-%m-%d %H:%M")}"
        values = [
            [contact_number, "AI", created_at]
        ]
        body = {
            "values": values
        }
        new_customer_addition = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=SPREADSHEET_ID,
                range='Conversations!A1',
                valueInputOption="USER_ENTERED",
                body=body,
                insertDataOption="INSERT_ROWS"
            )
            .execute()
        )
        return "agent_node"
                
    
    except HttpError as e:
        return "error executing human handoff request: error"


prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT), 
        MessagesPlaceholder(variable_name="messages", optional=True)
    ])

llm = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools_list)


def call_agent(state: AgentState):
    """Decide response using tools and context"""
    messages = state["messages"]
    chain = prompt | llm
    response = chain.invoke({"messages": messages})
    return {"messages": [response]}


workflow = StateGraph(AgentState)
workflow.add_node("agent_node", call_agent)
workflow.add_node("tool_node", tools_node)

workflow.add_conditional_edges(START, route_query)
workflow.add_conditional_edges("agent_node", tools_condition)
workflow.add_edge("tool_node", "agent_node")



REDIS_URL = os.getenv("REDIS_URL")


def get_bot_response(compiled_agent: CompiledStateGraph, user_message: str, user_phone: str) -> str:
    runtimeConfig = {
        "configurable": {
            "thread_id": user_phone,
            "phone_number": user_phone
        }
    }

    payload = {"messages": [HumanMessage(content=user_message)]}
    final_output = compiled_agent.invoke(payload, config=runtimeConfig)
    ai_response_text = final_output["messages"][-1].content
    return ai_response_text


# Loop for test
# while True:
#     query = input("Query: ")
#     if query == "end":
#         break
#     runtimeConfig = {
#         "configurable": {
#             "thread_id": 1,
#             "phone_number": 923268026207
#         }
#     }

#     payload = {"messages": [HumanMessage(content=query)]}
#     final_output = agent.invoke(payload, config=runtimeConfig)
#     ai_response_text = final_output["messages"][-1].content
#     print(f"AI: {ai_response_text}")


