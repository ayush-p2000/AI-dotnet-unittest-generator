import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def find_dotnet() -> Optional[str]:
    """
    Locates the dotnet executable across PATH and standard installation directories.
    Returns the absolute path to dotnet, or None if not found.
    """
    # 1. Check if 'dotnet' is available in current PATH
    found = shutil.which("dotnet")
    if found:
        return str(Path(found).resolve())

    # 2. Check DOTNET_ROOT if set in environment
    dotnet_root = os.environ.get("DOTNET_ROOT")
    if dotnet_root:
        bin_name = "dotnet.exe" if sys.platform == "win32" else "dotnet"
        cand = Path(dotnet_root) / bin_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand.resolve())

    # 3. Check well-known installation paths based on OS
    candidates = []
    if sys.platform == "darwin":  # macOS
        candidates = [
            Path.home() / ".gemini" / "antigravity-ide" / "bin" / "dotnet",
            Path.home() / ".dotnet" / "dotnet",
            Path("/Library/Frameworks/Python.framework/Versions/3.14/bin/dotnet"),
            Path("/usr/local/share/dotnet/dotnet"),
            Path("/opt/homebrew/bin/dotnet"),
            Path("/usr/local/bin/dotnet"),
        ]
    elif sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "dotnet.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "dotnet" / "dotnet.exe",
            Path.home() / ".dotnet" / "dotnet.exe",
            Path.home() / "AppData" / "Local" / "Microsoft" / "dotnet" / "dotnet.exe",
        ]
    else:  # Linux / Unix
        candidates = [
            Path.home() / ".dotnet" / "dotnet",
            Path("/usr/share/dotnet/dotnet"),
            Path("/usr/bin/dotnet"),
            Path("/usr/local/bin/dotnet"),
            Path("/snap/bin/dotnet"),
        ]

    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand.resolve())

    return None


def ensure_dotnet_env() -> Optional[str]:
    """
    Ensures DOTNET_ROOT and PATH in os.environ are configured so that child
    processes and subprocesses can find 'dotnet'.
    Returns the resolved path to dotnet if found, or None.
    """
    dotnet_bin = find_dotnet()
    if not dotnet_bin:
        return None

    dotnet_dir = str(Path(dotnet_bin).parent)

    # Set DOTNET_ROOT if not present
    if "DOTNET_ROOT" not in os.environ:
        # If dotnet is in ~/.dotnet or standard dotnet dir, use that as root
        if (Path(dotnet_dir) / "shared").is_dir() or (Path(dotnet_dir) / "host").is_dir():
            os.environ["DOTNET_ROOT"] = dotnet_dir
        elif (Path.home() / ".dotnet").is_dir():
            os.environ["DOTNET_ROOT"] = str(Path.home() / ".dotnet")

    # Prepend dotnet directory and dotnet tools to PATH if not already in PATH
    current_path = os.environ.get("PATH", "")
    paths = current_path.split(os.pathsep)

    candidates_to_add = [
        str(Path.home() / ".gemini" / "antigravity-ide" / "bin"),
        str(Path.home() / ".dotnet"),
        str(Path.home() / ".dotnet" / "tools"),
        dotnet_dir,
    ]
    to_add = [p for p in candidates_to_add if p not in paths and Path(p).is_dir()]

    if to_add:
        os.environ["PATH"] = os.pathsep.join(to_add) + (os.pathsep + current_path if current_path else "")

    return dotnet_bin


def get_dotnet_cmd(required: bool = True) -> str:
    """
    Returns the dotnet command path to execute.
    If required=True and dotnet cannot be found, raises a descriptive FileNotFoundError.
    """
    dotnet_bin = ensure_dotnet_env()
    if dotnet_bin:
        return dotnet_bin

    if required:
        error_msg = (
            ".NET SDK ('dotnet') was not found on your system.\n\n"
            "Please install the .NET SDK (>= 8.0) or add it to your PATH:\n"
        )
        if sys.platform == "darwin":
            error_msg += (
                "  - Homebrew: brew install --cask dotnet-sdk\n"
                "  - Download installer: https://dotnet.microsoft.com/download\n"
                "  - If installed in ~/.dotnet, add to ~/.zshrc:\n"
                "      export DOTNET_ROOT=\"$HOME/.dotnet\"\n"
                "      export PATH=\"$DOTNET_ROOT:$DOTNET_ROOT/tools:$PATH\"\n"
            )
        elif sys.platform == "win32":
            error_msg += (
                "  - Winget: winget install Microsoft.DotNet.SDK.8\n"
                "  - Download installer: https://dotnet.microsoft.com/download\n"
            )
        else:
            error_msg += (
                "  - Ubuntu/Debian: sudo apt-get install -y dotnet-sdk-8.0\n"
                "  - Download installer: https://dotnet.microsoft.com/download\n"
            )
        raise FileNotFoundError(error_msg)

    return "dotnet"


# Automatically ensure dotnet environment when this module is imported
ensure_dotnet_env()
