"""mangaeasy.web.app — the mangaEasy control center.

``mangaeasy app`` opens a desktop window (pywebview) wrapping a local Flask
UI. If pywebview or a GUI backend is unavailable, it falls back to the
browser.

The package is split by concern:

    state.py        shared state + persistence (survives restarts)
    jobs.py         subprocess job / editor process management
    api_setup.py    prerequisite checks + one-click AI tool installs
    api_project.py  project folder, config files, remembered UI state
    api_fs.py       folder & file pickers, open-in-file-manager
    api_run.py      run pipeline / chapter commands, editors, status
    server.py       startup: desktop window or browser

All endpoints bind to 127.0.0.1 only.
"""

from __future__ import annotations

from flask import Flask, render_template

from mangaeasy import __version__
from mangaeasy.web.flask_utils import register_shutdown
from mangaeasy.web.app.state import ASSETS, broadcaster


def create_app() -> Flask:
    flask_app = Flask(
        __name__,
        # Always the packaged assets — the control center must not be shadowed
        # by a project-local templates/ folder the way the editors are.
        template_folder=str(ASSETS / "templates"),
        static_folder=str(ASSETS / "static"),
    )
    register_shutdown(flask_app)
    broadcaster.register_route(flask_app)

    from mangaeasy.web.app import api_fs, api_project, api_run, api_setup

    flask_app.register_blueprint(api_setup.bp)
    flask_app.register_blueprint(api_project.bp)
    flask_app.register_blueprint(api_fs.bp)
    flask_app.register_blueprint(api_run.bp)

    @flask_app.route("/")
    def index():
        return render_template("app.html", version=__version__)

    return flask_app


app = create_app()


def main() -> int:
    from mangaeasy.web.app.server import run

    return run(app)


if __name__ == "__main__":
    raise SystemExit(main())
