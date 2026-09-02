import os
import subprocess
from pathlib import Path

WORKSPACE_DIR = Path.cwd()

def is_safe_path(target_path: Path) -> bool:
    """Ensure the agent doesn't try to read/write outside the workspace."""
    try:
        return WORKSPACE_DIR in target_path.resolve().parents or target_path.resolve() == WORKSPACE_DIR
    except Exception:
        return False

def list_directory(path: str = ".") -> str:
    """Lists the contents of a directory. Use this to explore the project structure."""
    target = (WORKSPACE_DIR / path).resolve()
    if not is_safe_path(target):
        return "Error: Path access denied."
    
    try:
        items = os.listdir(target)
        return "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def read_file(filepath: str) -> str:
    """Reads the contents of a file."""
    target = (WORKSPACE_DIR / filepath).resolve()
    if not is_safe_path(target):
        return "Error: Path access denied."
    
    try:
        with open(target, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_file(filepath: str, content: str) -> str:
    """Writes content to a file, creating directories if needed and overwriting existing content."""
    target = (WORKSPACE_DIR / filepath).resolve()
    if not is_safe_path(target):
        return "Error: Path access denied."
    
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

def run_shell_command(command: str) -> str:
    """Executes a shell command (like python scripts, pytest, or ls) and returns the terminal output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=15 
        )
        # Return both stdout and stderr so the agent sees errors if code fails
        output = result.stdout if result.stdout else result.stderr
        return output.strip() or "Command executed successfully with no output."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 15 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"