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
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PromptStyle

# Constants
MAX_FILE_SIZE = 5_000_000  # 5MB
MAX_FILES_TO_PROCESS = 1000
EXCLUDED_FILES = {
    ".DS_Store", "Thumbs.db", ".gitignore", ".python-version", "uv.lock", ".uv", "uvenv", ".uvenv", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".coverage", ".mypy_cache", "node_modules", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", ".next", ".nuxt", "dist", "build", ".cache", ".parcel-cache", ".turbo", ".vercel", ".output",
    ".contentlayer", "out", "coverage", ".nyc_output", "storybook-static", ".env", ".env.local", ".env.development",
    ".env.production", ".git", ".svn", ".hg", "CVS"
}
EXCLUDED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".avif", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".bin", ".pdf", ".doc", ".docx", ".xls",
    ".xlsx", ".ppt", ".pptx", ".pyc", ".pyo", ".pyd", ".egg", ".whl", ".uv", ".uvenv", ".db", ".sqlite", ".sqlite3",
    ".log", ".idea", ".vscode", ".map", ".chunk.js", ".chunk.css", ".min.js", ".min.css", ".bundle.js", ".bundle.css",
    ".cache", ".tmp", ".temp", ".ttf", ".otf", ".woff", ".woff2", ".eot"
}

# Initialize Rich console and prompt session
console = Console()
prompt_session = PromptSession(
    style=PromptStyle.from_dict({
        'prompt': '#00aa00 bold',  # Green prompt
    })
)

# Load environment variables
load_dotenv()

# Configure OpenAI client
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

# Pydantic Models
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

# System Prompt
SYSTEM_PROMPT = dedent("""\
    You are an elite software engineer called DeepSeek Engineer with decades of experience across all programming domains.
    Your expertise spans system design, algorithms, testing, and best practices.
    You provide thoughtful, well-structured solutions while explaining your reasoning.

    Core capabilities:
    1. Code Analysis & Discussion
    2. File Operations:
       a) Read existing files
       b) Create new files
       c) Edit existing files

    Output Format:
    You must provide responses in this JSON structure:
    {
      "assistant_reply": "Your main explanation or response",
      "files_to_create": [{"path": "path/to/new/file", "content": "complete file content"}],
      "files_to_edit": [{"path": "path/to/existing/file", "original_snippet": "exact code to be replaced", "new_snippet": "new code to insert"}]
    }

    Guidelines:
    1. YOU ONLY RETURN JSON, NO OTHER TEXT OR EXPLANATION OUTSIDE THE JSON!!!
    2. Follow language-specific best practices.
    3. Suggest tests or validation steps when appropriate.
""")

# Conversation state
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

# Helper Functions
def read_local_file(file_path: str) -> str:
    """Read and return the content of a local file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def create_file(path: str, content: str) -> None:
    """Create or overwrite a file with the given content."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[green]✓[/green] Created/updated file at '[cyan]{file_path}[/cyan]'")

def apply_diff_edit(path: str, original_snippet: str, new_snippet: str) -> None:
    """Apply a diff edit to a file."""
    try:
        content = read_local_file(path)
        updated_content = content.replace(original_snippet, new_snippet, 1)
        create_file(path, updated_content)
        console.print(f"[green]✓[/green] Applied diff edit to '[cyan]{path}[/cyan]'")
    except FileNotFoundError:
        console.print(f"[red]✗[/red] File not found: '[cyan]{path}[/cyan]'", style="red")
    except ValueError as e:
        console.print(f"[yellow]⚠[/yellow] {str(e)} in '[cyan]{path}[/cyan]'. No changes made.", style="yellow")

def add_file_to_conversation(file_path: str) -> None:
    """Add a file's content to the conversation history."""
    try:
        content = read_local_file(file_path)
        conversation_history.append({
            "role": "system",
            "content": f"Content of file '{file_path}':\n\n{content}"
        })
        console.print(f"[green]✓[/green] Added file '[cyan]{file_path}[/cyan]' to conversation.\n")
    except OSError as e:
        console.print(f"[red]✗[/red] Could not add file '[cyan]{file_path}[/cyan]': {e}\n", style="red")

def add_directory_to_conversation(directory_path: str) -> None:
    """Add all files in a directory to the conversation history."""
    with console.status("[bold green]Scanning directory...") as status:
        skipped_files = []
        added_files = []
        total_files_processed = 0

        for root, dirs, files in os.walk(directory_path):
            if total_files_processed >= MAX_FILES_TO_PROCESS:
                console.print(f"[yellow]⚠[/yellow] Reached maximum file limit ({MAX_FILES_TO_PROCESS})")
                break

            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in EXCLUDED_FILES]

            for file in files:
                if total_files_processed >= MAX_FILES_TO_PROCESS:
                    break

                if file.startswith('.') or file in EXCLUDED_FILES:
                    skipped_files.append(os.path.join(root, file))
                    continue

                _, ext = os.path.splitext(file)
                if ext.lower() in EXCLUDED_EXTENSIONS:
                    skipped_files.append(os.path.join(root, file))
                    continue

                full_path = os.path.join(root, file)
                try:
                    if os.path.getsize(full_path) > MAX_FILE_SIZE:
                        skipped_files.append(f"{full_path} (exceeds size limit)")
                        continue

                    if is_binary_file(full_path):
                        skipped_files.append(full_path)
                        continue

                    add_file_to_conversation(full_path)
                    added_files.append(full_path)
                    total_files_processed += 1
                except OSError:
                    skipped_files.append(full_path)

        console.print(f"[green]✓[/green] Added folder '[cyan]{directory_path}[/cyan]' to conversation.")
        if added_files:
            console.print(f"\n[bold]Added files:[/bold] ({len(added_files)} of {total_files_processed})")
            for f in added_files:
                console.print(f"[cyan]{f}[/cyan]")
        if skipped_files:
            console.print(f"\n[yellow]Skipped files:[/yellow] ({len(skipped_files)})")
            for f in skipped_files:
                console.print(f"[yellow]{f}[/yellow]")
        console.print()

def is_binary_file(file_path: str, peek_size: int = 1024) -> bool:
    """Check if a file is binary."""
    try:
        with open(file_path, 'rb') as f:
            return b'\0' in f.read(peek_size)
    except Exception:
        return True

def normalize_path(path_str: str) -> str:
    """Normalize and validate a file path."""
    path = Path(path_str).resolve()
    if ".." in path.parts:
        raise ValueError(f"Invalid path: {path_str} contains parent directory references")
    return str(path)

# Main Interactive Loop
def main() -> None:
    console.print(Panel.fit(
        "[bold blue]Welcome to Deep Seek Engineer with Structured Output[/bold blue] [green](and CoT reasoning)[/green]!🐋",
        border_style="blue"
    ))
    console.print(
        "Use '[bold magenta]/add[/bold magenta]' to include files in the conversation:\n"
        "  • '[bold magenta]/add path/to/file[/bold magenta]' for a single file\n"
        "  • '[bold magenta]/add path/to/folder[/bold magenta]' for all files in a folder\n"
        "Type '[bold red]exit[/bold red]' or '[bold red]quit[/bold red]' to end.\n"
    )

    while True:
        try:
            user_input = prompt_session.prompt("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Exiting.[/yellow]")
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            console.print("[yellow]Goodbye![/yellow]")
            break

        if user_input.strip().lower().startswith("/add "):
            path_to_add = user_input[len("/add "):].strip()
            try:
                normalized_path = normalize_path(path_to_add)
                if os.path.isdir(normalized_path):
                    add_directory_to_conversation(normalized_path)
                else:
                    add_file_to_conversation(normalized_path)
            except (OSError, ValueError) as e:
                console.print(f"[red]✗[/red] Could not add path '[cyan]{path_to_add}[/cyan]': {e}\n", style="red")
            continue

        # Handle OpenAI API interaction
        response_data = stream_openai_response(user_input)

        if response_data.files_to_create:
            for file_info in response_data.files_to_create:
                create_file(file_info.path, file_info.content)

        if response_data.files_to_edit:
            show_diff_table(response_data.files_to_edit)
            confirm = prompt_session.prompt("Do you want to apply these changes? (y/n): ").strip().lower()
            if confirm == 'y':
                for edit_info in response_data.files_to_edit:
                    apply_diff_edit(edit_info.path, edit_info.original_snippet, edit_info.new_snippet)
            else:
                console.print("[yellow]ℹ[/yellow] Skipped applying diff edits.", style="yellow")

    console.print("[blue]Session finished.[/blue]")

if __name__ == "__main__":
    main()
