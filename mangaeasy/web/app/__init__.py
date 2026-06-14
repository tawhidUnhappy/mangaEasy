"""mangaeasy.web.app — the mangaEasy control center.

``mangaeasy app`` opens a NiceGUI native desktop window.
The entire UI is driven by mangaeasy.web.nicegui_app; no separate Flask
server or pywebview wrapper is needed.

Legacy Flask modules (api_setup, api_project, …) still contain useful
logic that nicegui_app imports directly without going through HTTP.
"""

from __future__ import annotations


def main() -> int:
    from mangaeasy.web.nicegui_app import run
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
