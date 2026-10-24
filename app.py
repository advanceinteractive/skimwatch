#!/usr/bin/env python3
"""
app.py

Entry point for the packaged, double-click version of SkimWatch (the
one built with PyInstaller, which needs no Python on the user's computer).

It starts the same local web server as webapp.py, opens the browser, and
then keeps the program alive: on macOS as a normal Dock app (click the icon
to reopen the page, Quit to stop), elsewhere until its window is closed.
Running from source? Use webapp.py or the start-* launchers instead.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
import traceback
import urllib.request
import webbrowser
from pathlib import Path

import local_transcribe as lt
import webapp


def transcripts_dir() -> Path:
    """Documents/SkimWatch, or the home folder if Documents is off-limits."""
    home = Path.home()
    for base in (home / "Documents", home):
        folder = base / webapp.APP_NAME
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-test"
            probe.write_text("")
            probe.unlink()
            return folder
        except OSError:
            continue
    return home


def log_path() -> Path:
    if sys.platform == "darwin":
        folder = Path.home() / "Library" / "Logs"
    else:
        folder = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / webapp.APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "SkimWatch.log"


def already_running(port: int) -> bool:
    """True if another copy of this app is answering on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1) as res:
            return json.load(res).get("app") == webapp.APP_NAME
    except Exception:
        return False


def run_mac_app(url: str) -> None:
    """Behave like a normal Mac app: Dock icon, menu, Cmd+Q."""
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSMenu,
        NSMenuItem,
        NSObject,
    )

    class Delegate(NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
            webbrowser.open(url)  # clicking the Dock icon brings the page back
            return False

        def openPage_(self, sender):
            webbrowser.open(url)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)

    menubar = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    menubar.addItem_(app_item)
    menu = NSMenu.alloc().init()
    open_item = menu.addItemWithTitle_action_keyEquivalent_("Open in Browser", "openPage:", "o")
    open_item.setTarget_(delegate)
    menu.addItem_(NSMenuItem.separatorItem())
    menu.addItemWithTitle_action_keyEquivalent_(f"Quit {webapp.APP_NAME}", "terminate:", "q")
    app_item.setSubmenu_(menu)
    app.setMainMenu_(menubar)

    app.run()


def main() -> None:
    multiprocessing.freeze_support()
    no_browser = "--no-browser" in sys.argv[1:]

    if already_running(webapp.DEFAULT_PORT):
        webbrowser.open(f"http://127.0.0.1:{webapp.DEFAULT_PORT}/")
        return

    server = webapp.create_server(webapp.DEFAULT_PORT, transcripts_dir())
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()

    if sys.platform == "darwin":
        run_mac_app(url)
    else:
        print(f"{webapp.APP_NAME} {lt.__version__} is running at {url}")
        print("Close this window to stop.", flush=True)
        threading.Event().wait()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with log_path().open("a", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        raise
