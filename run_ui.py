#!/usr/bin/env python3
"""
Launcher for Nemotron C# Studio Web UI.
Starts the local backend server and automatically opens the UI in your default browser.
"""

import argparse
import socket
import sys
import threading
import time
import webbrowser

from ui_server import run_server


def find_free_port(preferred_port: int = 5000) -> int:
    """Checks if preferred port is free; if not, picks an available port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred_port))
            return preferred_port
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description="Start Nemotron C# Studio Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Port to run the UI server on (default: 5000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args()

    port = find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    print("======================================================================")
    print("  STARTING NEMOTRON C# STUDIO (AI TEST GEN & SONAR RESOLVER)")
    print("======================================================================")
    print(f"  Web Interface: {url}")
    print("  Press Ctrl+C to stop the server at any time.")
    print("======================================================================")

    # Launch browser after slight delay
    if not args.no_browser:
        def open_browser():
            time.sleep(1.0)
            webbrowser.open_new_tab(url)

        threading.Thread(target=open_browser, daemon=True).start()

    try:
        run_server(port)
    except KeyboardInterrupt:
        print("\nStudio server stopped gracefully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
