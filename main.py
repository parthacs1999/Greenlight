import os
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown

# Import the new GenAI SDK 
from google import genai
from google.genai import types

# Import our custom tools
from tools import list_directory, read_file, write_file, run_shell_command

load_dotenv()

app = typer.Typer()
console = Console()

SYSTEM_INSTRUCTION = """
You are an elite, autonomous terminal-native coding agent. 
You have access to local file system tools and the shell. 
When asked to build or fix something, follow this loop:
1. EXPLORE: Use list_directory and read_file to understand the current state.
2. EXECUTE: Use write_file to create or modify code.
3. VERIFY: Use run_shell_command to run tests or execute the code to ensure it works.

If a command or script fails, read the error output and iteratively fix the code until it succeeds.
Be concise in your chat responses. Let the tools do the work.
"""

@app.command()
def chat():
    """Start the interactive terminal agent."""
    if not os.environ.get("GEMINI_API_KEY"):
        console.print("[bold red]Error:[/bold red] GEMINI_API_KEY environment variable not set in .env or shell.")
        return

    console.print(Panel.fit("[bold green]Terminal-Native Coding Agent[/bold green]\nType 'exit' to stop.", border_style="green"))
    
    # Initialize the client. It automatically picks up GEMINI_API_KEY.
    client = genai.Client()
    
    # Define our list of Python functions. 
    # The SDK automatically converts these into a tool schema for Gemini.
    agent_tools = [list_directory, read_file, write_file, run_shell_command]
    
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=agent_tools,
        temperature=0.2, # Low temperature for logical, deterministic coding
    )

    # Initialize a multi-turn chat session with the configuration
    chat_session = client.chats.create(
        model="gemini-3.5-flash",
        config=config
    )
    
    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]You[/bold blue]")
            
            if user_input.lower() in ['exit', 'quit']:
                console.print("[bold red]Shutting down agent...[/bold red]")
                break
                
            # The spinner shows while Gemini processes text AND executes functions locally
            with console.status("[bold cyan]Agent is thinking and executing tools...[/bold cyan]", spinner="dots"):
                # send_message automatically handles the back-and-forth function calling loop
                response = chat_session.send_message(user_input)
            
            console.print("\n[bold magenta]Agent:[/bold magenta]")
            console.print(Markdown(response.text))
            
        except KeyboardInterrupt:
            console.print("\n[bold red]Shutting down agent...[/bold red]")
            break
        except Exception as e:
            console.print(f"\n[bold red]Agent Error:[/bold red] {str(e)}")

if __name__ == "__main__":
    app()