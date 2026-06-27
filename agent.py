import csv
import os
from datetime import datetime
from pathlib import Path 

from dotenv import load_dotenv
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

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
def create_repair_order(customer_name: str, device: str, issue_description: str, contact_number: str) -> str:
    """Create a new repair order, Use this tool ONLY when you have collected ALL four pieces of information from 
    the customer: their name, device model, issue description, and contact phone number"""

    #generate a unique order id using the current timestamp
    order_id = f"ORD-{datetime.now().strftime("%Y%m%d%H%M%S")}"

    #append the order to the CSV file, creating headers if the file is new

    file_exists = ORDERS_CSV_PATH.exists()

    with open(ORDERS_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["order_id", "customer_name", "device", "issue_description", "contact_number", "created_at", "status"])
        writer.writerow([order_id, customer_name, device, issue_description, contact_number, datetime.now().isoformat(), "pending"])

        return f"order created successfully! Order ID: {order_id}. We will contact {customer_name} at {contact_number} with a quote shortly"
    
SYSTEM_PROMPT = """You are the friendly customer support assistant for TechFix 
Laptop Repair shop.
Your job is to:
1. Answer customer questions about services, pricing, turnaround times, location, and policies using the lookup_services tool.
2. Help customers place repair orders by collecting their information through natural conversation.

When a customer wants to book a repair:
- Ask for their name (if not provided)
- Ask for their device model (e.g., "Dell XPS 15", "MacBook Pro 2021" p.s it doesnt have to be the full model, only the company name works fine too)
- Ask for a description of the issue (accept short symptoms like "cracked screen" or "won't turn on" as a valid description)
- Ask for their contact phone number
- If the customer provides multiple pieces of info at once in their message, extract them all immediately. Do not re-ask for details they already mentioned.
- Only call create_repair_order when you have ALL four pieces of info

CRITICAL RULES FOR TOOL CALLING:
1. If the customer provides multiple pieces of information in a single sentence, extract ALL of them immediately.
2. Do not re-ask or double-check information that was already clearly stated in their message history.
3. As long as you have something written for all 4 fields (even if partial), trigger 'create_repair_order' immediately. Do not stall.

Keep responses concise and friendly. Use emojis sparingly. Always be helpful."""

def build_agent() -> AgentExecutor:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    tools = [lookup_services, create_repair_order]
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT), 
        MessagesPlaceholder(variable_name="chat_history", optional=True),
        ("human", "{input}"), MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)

agent_executor = build_agent()

def get_bot_response(user_message: str) -> str:
    result = agent_executor.invoke({"input": user_message})
    return result["output"]