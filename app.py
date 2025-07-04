import os
import gradio as gr
import requests
import inspect
import pandas as pd
import dotenv

from contextlib import redirect_stdout 
from typing import Optional, TypedDict, Annotated

# ── LangGraph / LangChain core ───────────────────────
from langgraph.graph import StateGraph, START, END   
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, SystemMessage, BaseMessage, AnyMessage 
from langchain_core.tools import Tool  
from langchain_openai import ChatOpenAI    
from langchain_tavily import TavilySearch
#from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_community.utilities import WikipediaAPIWrapper, ArxivAPIWrapper
from langchain_community.tools import YouTubeSearchTool

# Costum tools
from tools.download_file import DownloadFile#
from tools.web_search import WebSearchTool#
from tools.chess_tool import ChessTool#
from tools.audio_tool import AudioTool#

# (Keep Constants as is)
# --- Constants ---
DEFAULT_API_URL = "https://agents-course-unit4-scoring.hf.space"
dotenv.load_dotenv()  



class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

# --- Basic Agent Definition ---
# ----- THIS IS WERE YOU CAN BUILD WHAT YOU WANT ------

class BasicAgent:
    """You are an agent to answer questions in a way you were asked to. USE TOOLS if needed."""
    
    def __init__(self):
        
        # 1. LLM (adjust if you prefer another model)
        #llm = ChatOpenAI(model="gpt-4o-mini", temperature=0) 
        llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0) 
        
        # 2. Define System Prompt
        self.sys_msg = SystemMessage(content="You are a helpful AI agent designed to answer questions. Use TOOLS when helpful. Start your answer with 'FINAL ANSWER: ' and remove anything before that. Extract just the answer line for submission. your final answer should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. Remove EVERYTHING not needed from the answer. If you are asked for a number, don't use comma to write your number neither use units such as $ or percent sign unless specified otherwise. If you are asked for a string, don't use articles, neither abbreviations (e.g. for cities), and write the digits in plain text unless specified otherwise. If you are asked for a comma separated list, apply the above rules depending of whether the element to be put in the list is a number or a string. If you are asked to return the first name, return the first name only (Sima and not Sima Jamali). If you are asked to give a name of a city without abbreviation, do it (for example, St. Petersburg should be Saint Petersburg).  If the answer is comma separated, there must always be a space between the comma and the next letter. ")

        # 3. tools list
        tools = [TavilySearch(),]
        
        tools += [YouTubeSearchTool(), DownloadFile(),  WebSearchTool(), ChessTool(), AudioTool(), ]
    
        wiki = WikipediaAPIWrapper()    # free, no key needed
        wiki_tool =  Tool.from_function(
            name="wikipedia_search",
            description="A wrapper around Wikipedia. Useful for when you need to answer general questions about people, places, companies, historical events, or any other topics. Input should be a search query.",
            func=wiki.run,
        )
        
        arxiv = ArxivAPIWrapper() # free, no key needed
        arxiv_tool = Tool(
            name="Arxiv",
            description="A wrapper around Arxiv. Useful for when you need to answer questions about academic papers. Input should be a search query.",
            func=arxiv.run,
        )
        tools += [wiki_tool]
        
        # 4. Equip the llm with tools
        self.llm_with_tools = llm.bind_tools(tools)
         
            
        # 5. Build the LangGraph
        builder = StateGraph(AgentState)

        builder.add_node("assistant", self._assistant)
        builder.add_node("tools", ToolNode(tools))
        
        builder.add_edge(START, "assistant")
        builder.add_conditional_edges(
            "assistant",
            tools_condition,     # must return "tools" or END
            {
                "__end__": END,
                "tools": "tools", 
            }
        )
        builder.add_edge("tools", "assistant")

        self.graph = builder.compile()

    # ---- Graph node functions -----------------------------------

    def _assistant(self, state: AgentState):
        """You are precise agent. Use tools when needed. use tavilysearch tool first and then other tools if needed. your final answer should be a number OR as few words as possible OR a comma separated list of numbers and/or strings. Remove EVERYTHING not needed from the answer.
        """

        resp = self.llm_with_tools.invoke(state["messages"])
        return {"messages": [resp]}
        


    # ---- External call interface --------------------------------

    def __call__(self, question: str) -> str:
        messages = [self.sys_msg, HumanMessage(content=question)]
        response = self.graph.invoke({"messages": messages})#, config={"recursion_limit": 10})
        raw_answer = response['messages'][-1].content
        
        # Return anything AFTER "final answer: "
        PREFIX = "FINAL ANSWER:"
        idx = raw_answer.upper().find(PREFIX)   # case-insensitive search
        if idx != -1:                           # PREFIX found anywhere
            return raw_answer[idx + len(PREFIX):].lstrip()
        return raw_answer                       # PREFIX not present




def run_and_submit_all( profile: gr.OAuthProfile | None):
    """
    Fetches all questions, runs the BasicAgent on them, submits all answers,
    and displays the results.
    """
    # --- Determine HF Space Runtime URL and Repo URL ---
    space_id = os.getenv("SPACE_ID") # Get the SPACE_ID for sending link to the code

    if profile:
        username= f"{profile.username}"
        print(f"User logged in: {username}")
    else:
        print("User not logged in.")
        return "Please Login to Hugging Face with the button.", None

    api_url = DEFAULT_API_URL
    questions_url = f"{api_url}/questions"
    submit_url = f"{api_url}/submit"

    # 1. Instantiate Agent ( modify this part to create your agent)
    try:
        agent = BasicAgent()
    except Exception as e:
        print(f"Error instantiating agent: {e}")
        return f"Error initializing agent: {e}", None
    # In the case of an app running as a hugging Face space, this link points toward your codebase ( usefull for others so please keep it public)
    agent_code = f"https://huggingface.co/spaces/{space_id}/tree/main"
    print(agent_code)

    # 2. Fetch Questions
    print(f"Fetching questions from: {questions_url}")
    try:
        response = requests.get(questions_url, timeout=15)
        response.raise_for_status()
        questions_data = response.json()
        if not questions_data:
             print("Fetched questions list is empty.")
             return "Fetched questions list is empty or invalid format.", None
        print(f"Fetched {len(questions_data)} questions.")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching questions: {e}")
        return f"Error fetching questions: {e}", None
    except requests.exceptions.JSONDecodeError as e:
         print(f"Error decoding JSON response from questions endpoint: {e}")
         print(f"Response text: {response.text[:500]}")
         return f"Error decoding server response for questions: {e}", None
    except Exception as e:
        print(f"An unexpected error occurred fetching questions: {e}")
        return f"An unexpected error occurred fetching questions: {e}", None

    # 3. Run your Agent
    results_log = []
    answers_payload = []
    print(f"Running agent on {len(questions_data)} questions...")
    for item in questions_data:
        task_id = item.get("task_id")
        question_text = item.get("question")
        if not task_id or question_text is None:
            print(f"Skipping item with missing task_id or question: {item}")
            continue
        try:
            submitted_answer = agent(question_text)
            answers_payload.append({"task_id": task_id, "submitted_answer": submitted_answer})
            results_log.append({"Task ID": task_id, "Question": question_text, "Submitted Answer": submitted_answer})
        except Exception as e:
             print(f"Error running agent on task {task_id}: {e}")
             results_log.append({"Task ID": task_id, "Question": question_text, "Submitted Answer": f"AGENT ERROR: {e}"})

    if not answers_payload:
        print("Agent did not produce any answers to submit.")
        return "Agent did not produce any answers to submit.", pd.DataFrame(results_log)

    # 4. Prepare Submission 
    submission_data = {"username": username.strip(), "agent_code": agent_code, "answers": answers_payload}
    status_update = f"Agent finished. Submitting {len(answers_payload)} answers for user '{username}'..."
    print(status_update)

    # 5. Submit
    print(f"Submitting {len(answers_payload)} answers to: {submit_url}")
    try:
        response = requests.post(submit_url, json=submission_data, timeout=60)
        response.raise_for_status()
        result_data = response.json()
        final_status = (
            f"Submission Successful!\n"
            f"User: {result_data.get('username')}\n"
            f"Overall Score: {result_data.get('score', 'N/A')}% "
            f"({result_data.get('correct_count', '?')}/{result_data.get('total_attempted', '?')} correct)\n"
            f"Message: {result_data.get('message', 'No message received.')}"
        )
        print("Submission successful.")
        results_df = pd.DataFrame(results_log)
        return final_status, results_df
    except requests.exceptions.HTTPError as e:
        error_detail = f"Server responded with status {e.response.status_code}."
        try:
            error_json = e.response.json()
            error_detail += f" Detail: {error_json.get('detail', e.response.text)}"
        except requests.exceptions.JSONDecodeError:
            error_detail += f" Response: {e.response.text[:500]}"
        status_message = f"Submission Failed: {error_detail}"
        print(status_message)
        results_df = pd.DataFrame(results_log)
        return status_message, results_df
    except requests.exceptions.Timeout:
        status_message = "Submission Failed: The request timed out."
        print(status_message)
        results_df = pd.DataFrame(results_log)
        return status_message, results_df
    except requests.exceptions.RequestException as e:
        status_message = f"Submission Failed: Network error - {e}"
        print(status_message)
        results_df = pd.DataFrame(results_log)
        return status_message, results_df
    except Exception as e:
        status_message = f"An unexpected error occurred during submission: {e}"
        print(status_message)
        results_df = pd.DataFrame(results_log)
        return status_message, results_df


# --- Build Gradio Interface using Blocks ---
with gr.Blocks() as demo:
    gr.Markdown("# Basic Agent Evaluation Runner")
    gr.Markdown(
        """
        **Instructions:**

        1.  Please clone this space, then modify the code to define your agent's logic, the tools, the necessary packages, etc ...
        2.  Log in to your Hugging Face account using the button below. This uses your HF username for submission.
        3.  Click 'Run Evaluation & Submit All Answers' to fetch questions, run your agent, submit answers, and see the score.

        ---
        **Disclaimers:**
        Once clicking on the "submit button, it can take quite some time ( this is the time for the agent to go through all the questions).
        This space provides a basic setup and is intentionally sub-optimal to encourage you to develop your own, more robust solution. For instance for the delay process of the submit button, a solution could be to cache the answers and submit in a seperate action or even to answer the questions in async.
        """
    )

    gr.LoginButton()

    run_button = gr.Button("Run Evaluation & Submit All Answers")

    status_output = gr.Textbox(label="Run Status / Submission Result", lines=5, interactive=False)
    # Removed max_rows=10 from DataFrame constructor
    results_table = gr.DataFrame(label="Questions and Agent Answers", wrap=True)

    run_button.click(
        fn=run_and_submit_all,
        outputs=[status_output, results_table]
    )

if __name__ == "__main__":
    print("\n" + "-"*30 + " App Starting " + "-"*30)
    # Check for SPACE_HOST and SPACE_ID at startup for information
    space_host_startup = os.getenv("SPACE_HOST")
    space_id_startup = os.getenv("SPACE_ID") # Get SPACE_ID at startup

    if space_host_startup:
        print(f"✅ SPACE_HOST found: {space_host_startup}")
        print(f"   Runtime URL should be: https://{space_host_startup}.hf.space")
    else:
        print("ℹ️  SPACE_HOST environment variable not found (running locally?).")

    if space_id_startup: # Print repo URLs if SPACE_ID is found
        print(f"✅ SPACE_ID found: {space_id_startup}")
        print(f"   Repo URL: https://huggingface.co/spaces/{space_id_startup}")
        print(f"   Repo Tree URL: https://huggingface.co/spaces/{space_id_startup}/tree/main")
    else:
        print("ℹ️  SPACE_ID environment variable not found (running locally?). Repo URL cannot be determined.")

    print("-"*(60 + len(" App Starting ")) + "\n")

    print("Launching Gradio Interface for Basic Agent Evaluation...")
    demo.launch(debug=True, share=False)