#!/usr/bin/env python3

import os
import sys
import json
from pathlib import Path
from textwrap import dedent
from typing import List, Dict, Any, Optional
from openai import OpenAI
from pydantic import BaseModel
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.style import Style

# Initialize Rich console
console = Console()

# --------------------------------------------------------------------------------
# 1. Configure OpenAI client and load environment variables
# --------------------------------------------------------------------------------
load_dotenv()  # Load environment variables from .env file
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)  # Configure for DeepSeek API

# --------------------------------------------------------------------------------
# 2. Define our schema using Pydantic for type safety
# --------------------------------------------------------------------------------
class FileToCreate(BaseModel):
    path: str
    content: str

class FileToEdit(BaseModel):
    path: str
    original_snippet: str
    new_snippet: str

class AssistantResponse(BaseModel):
    assistant_reply: str
    files_to_create: Optional[List[FileToCreate]] = None
    files_to_edit: Optional[List[FileToEdit]] = None

# --------------------------------------------------------------------------------
# 3. System prompt
# --------------------------------------------------------------------------------
system_PROMPT = dedent("""\
    You are an elite software engineer called DeepSeek Engineer with decades of experience across all programming domains.
    Your expertise spans system design, algorithms, testing, and best practices.
    You provide thoughtful, well-structured solutions while explaining your reasoning.

    Core capabilities:
    1. Code Analysis & Discussion
       - Analyze code with expert-level insight
       - Explain complex concepts clearly
       - Suggest optimizations and best practices
       - Debug issues with precision

    2. File Operations:
       a) Read existing files
          - Access user-provided file contents for context
          - Analyze multiple files to understand project structure
       
       b) Create new files
          - Generate complete new files with proper structure
          - Create complementary files (tests, configs, etc.)
       
       c) Edit existing files
          - Make precise changes using diff-based editing
          - Modify specific sections while preserving context
          - Suggest refactoring improvements

    Output Format:
    You must provide responses in this JSON structure:
    {
      "assistant_reply": "Your main explanation or response",
      "files_to_create": [
        {
          "path": "path/to/new/file",
          "content": "complete file content"
        }
      ],
      "files_to_edit": [
        {
          "path": "path/to/existing/file",
          "original_snippet": "exact code to be replaced",
          "new_snippet": "new code to insert"
        }
      ]
    }

    Guidelines:
    1. For normal responses, use 'assistant_reply'
    2. When creating files, include full content in 'files_to_create'
    3. For editing files:
       - Use 'files_to_edit' for precise changes
       - Include enough context in original_snippet to locate the change
       - Ensure new_snippet maintains proper indentation
       - Prefer targeted edits over full file replacements
    4. Always explain your changes and reasoning
    5. Consider edge cases and potential impacts
    6. Follow language-specific best practices
    7. Suggest tests or validation steps when appropriate

    Remember: You're a senior engineer - be thorough, precise, and thoughtful in your solutions.
""")

# --------------------------------------------------------------------------------
# 4. Helper functions 
# --------------------------------------------------------------------------------

def read_local_file(file_path: str) -> str:
    """Return the text content of a local file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def create_file(path: str, content: str):
    """Create (or overwrite) a file at 'path' with the given 'content'."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)  # ensures any dirs exist
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[green]✓[/green] Created/updated file at '[cyan]{file_path}[/cyan]'")
    
    # Record the action
    conversation_history.append({
        "role": "assistant",
        "content": f"✓ Created/updated file at '{file_path}'"
    })
    
    # Add the actual content to conversation context
    normalized_path = normalize_path(str(file_path))
    conversation_history.append({
        "role": "system",
        "content": f"Content of file '{normalized_path}':\n\n{content}"
    })

def show_diff_table(files_to_edit: List[FileToEdit]) -> None:
    """Show the user a table of proposed edits and confirm."""
    if not files_to_edit:
        return
    
    # Enable multi-line rows by setting show_lines=True
    table = Table(title="Proposed Edits", show_header=True, header_style="bold magenta", show_lines=True)
    table.add_column("File Path", style="cyan")
    table.add_column("Original", style="red")
    table.add_column("New", style="green")

    for edit in files_to_edit:
        table.add_row(edit.path, edit.original_snippet, edit.new_snippet)
    
    console.print(table)

def apply_diff_edit(path: str, original_snippet: str, new_snippet: str):
    """Reads the file at 'path', replaces the first occurrence of 'original_snippet' with 'new_snippet', then overwrites."""
    try:
        content = read_local_file(path)
        if original_snippet in content:
            updated_content = content.replace(original_snippet, new_snippet, 1)
            create_file(path, updated_content)  # This will now also update conversation context
            console.print(f"[green]✓[/green] Applied diff edit to '[cyan]{path}[/cyan]'")
            conversation_history.append({
                "role": "assistant",
                "content": f"✓ Applied diff edit to '{path}'"
            })
        else:
            console.print(f"[yellow]⚠[/yellow] Original snippet not found in '[cyan]{path}[/cyan]'. No changes made.", style="yellow")
            console.print("\nExpected snippet:", style="yellow")
            console.print(Panel(original_snippet, title="Expected", border_style="yellow"))
            console.print("\nActual file content:", style="yellow")
            console.print(Panel(content, title="Actual", border_style="yellow"))
    except FileNotFoundError:
        console.print(f"[red]✗[/red] File not found for diff editing: '[cyan]{path}[/cyan]'", style="red")

def try_handle_add_command(user_input: str) -> bool:
    """
    If user_input starts with '/add ', read that file and insert its content
    into conversation as a system message. Returns True if handled; else False.
    """
    prefix = "/add "
    if user_input.strip().lower().startswith(prefix):
        file_path = user_input[len(prefix):].strip()
        try:
            content = read_local_file(file_path)
            conversation_history.append({
                "role": "system",
                "content": f"Content of file '{file_path}':\n\n{content}"
            })
            console.print(f"[green]✓[/green] Added file '[cyan]{file_path}[/cyan]' to conversation.\n")
        except OSError as e:
            console.print(f"[red]✗[/red] Could not add file '[cyan]{file_path}[/cyan]': {e}\n", style="red")
        return True
    return False

def ensure_file_in_context(file_path: str) -> bool:
    """
    Ensures the file content is in the conversation context.
    Returns True if successful, False if file not found.
    """
    try:
        normalized_path = normalize_path(file_path)
        content = read_local_file(normalized_path)
        file_marker = f"Content of file '{normalized_path}'"
        if not any(file_marker in msg["content"] for msg in conversation_history):
            conversation_history.append({
                "role": "system",
                "content": f"{file_marker}:\n\n{content}"
            })
        return True
    except OSError:
        console.print(f"[red]✗[/red] Could not read file '[cyan]{file_path}[/cyan]' for editing context", style="red")
        return False

def normalize_path(path_str: str) -> str:
    """Return a canonical, absolute version of the path."""
    return str(Path(path_str).resolve())

# --------------------------------------------------------------------------------
# 5. Conversation state
# --------------------------------------------------------------------------------
conversation_history = [
    {"role": "system", "content": system_PROMPT}
]

# --------------------------------------------------------------------------------
# 6. OpenAI API interaction with streaming
# --------------------------------------------------------------------------------

def guess_files_in_message(user_message: str) -> List[str]:
    """
    Attempt to guess which files the user might be referencing.
    Returns normalized absolute paths.
    """
    recognized_extensions = [".css", ".html", ".js", ".py", ".json", ".md"]
    potential_paths = []
    for word in user_message.split():
        if any(ext in word for ext in recognized_extensions) or "/" in word:
            path = word.strip("',\"")
            try:
                normalized_path = normalize_path(path)
                potential_paths.append(normalized_path)
            except (OSError, ValueError):
                continue
    return potential_paths

def stream_openai_response(user_message: str):
    """
    Streams the DeepSeek chat completion response and handles structured output.
    Returns the final AssistantResponse.
    """
    try:
        stream = client.chat.completions.create(
            model="deepseek-chat",
            messages=conversation_history,
            response_format={"type": "json_object"},
            max_completion_tokens=8000,
            stream=True
        )

        console.print("\nAssistant> ", style="bold blue", end="")
        full_content = ""

        for chunk in stream:
            if chunk.choices[0].delta.content:
                content_chunk = chunk.choices[0].delta.content
                full_content += content_chunk
                console.print(content_chunk, end="")

        console.print()

        # Validate response before parsing
        if not full_content.strip():
            error_msg = "Received empty response from API"
            console.print(f"[red]✗[/red] {error_msg}", style="red")
            return AssistantResponse(
                assistant_reply=error_msg,
                files_to_create=[],
                files_to_edit=[]
            )

        if not full_content.strip().startswith("{"):
            console.print(f"\n[red]✗[/red] Invalid JSON response received:", style="red")
            console.print(Panel(full_content, title="Raw Response", border_style="red"))
            return AssistantResponse(
                assistant_reply="Received non-JSON response from API",
                files_to_create=[],
                files_to_edit=[]
            )

        try:
            parsed_response = json.loads(full_content)
            return AssistantResponse(**parsed_response)
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON response: {e}"
            console.print(f"[red]✗[/red] {error_msg}", style="red")
            console.print(Panel(
                full_content,
                title="Invalid JSON Content",
                border_style="red",
                style="red"
            ))
            return AssistantResponse(
                assistant_reply="The assistant returned invalid JSON format",
                files_to_create=[],
                files_to_edit=[]
            )

    except Exception as e:
        error_msg = f"DeepSeek API error: {str(e)}"
        console.print(f"\n[red]✗[/red] {error_msg}", style="red")
        return AssistantResponse(
            assistant_reply=error_msg,
            files_to_create=[],
            files_to_edit=[]
        )

# --------------------------------------------------------------------------------
# 7. Main interactive loop
# --------------------------------------------------------------------------------

def main():
    console.print(Panel.fit(
        "[bold blue]Welcome to Deep Seek Engineer with Structured Output[/bold blue] [green](and streaming)[/green]!🐋",
        border_style="blue"
    ))
    console.print(
        "To include a file in the conversation, use '[bold magenta]/add path/to/file[/bold magenta]'.\n"
        "Type '[bold red]exit[/bold red]' or '[bold red]quit[/bold red]' to end.\n"
    )

    while True:
        try:
            user_input = console.input("[bold green]You>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Exiting.[/yellow]")
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            console.print("[yellow]Goodbye![/yellow]")
            break

        # If user is reading a file
        if try_handle_add_command(user_input):
            continue

        # Get streaming response from OpenAI (DeepSeek)
        response_data = stream_openai_response(user_input)

        # Create any files if requested
        if response_data.files_to_create:
            for file_info in response_data.files_to_create:
                create_file(file_info.path, file_info.content)

        # Show and confirm diff edits if requested
        if response_data.files_to_edit:
            show_diff_table(response_data.files_to_edit)
            confirm = console.input(
                "\nDo you want to apply these changes? ([green]y[/green]/[red]n[/red]): "
            ).strip().lower()
            if confirm == 'y':
                for edit_info in response_data.files_to_edit:
                    apply_diff_edit(edit_info.path, edit_info.original_snippet, edit_info.new_snippet)
            else:
                console.print("[yellow]ℹ[/yellow] Skipped applying diff edits.", style="yellow")

    console.print("[blue]Session finished.[/blue]")

if __name__ == "__main__":
    main()
