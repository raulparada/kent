# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from dataclasses import dataclass
import datetime
import gzip
import json
import logging
from logging.config import dictConfig
import os
from typing import Optional, Union
import uuid
import shutil
import os
import platform
import subprocess
import webbrowser
import pathlib
import zlib
import threading
import pathlib

from flask import Flask, request, render_template

from kent import __version__
from kent.utils import (
    parse_envelope,
    CorsMiddleware,
    sentry_dsn_to_envelope_url,
)

LOGGER = logging.getLogger(__name__)

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s: %(name)s %(message)s",
            }
        },
        "handlers": {
            "wsgi": {
                "class": "logging.StreamHandler",
                "stream": "ext://flask.logging.wsgi_errors_stream",
                "formatter": "default",
            },
        },
        "loggers": {
            "kent": {"level": "INFO"},
            "werkzeug": {"level": "ERROR"},
        },
        "root": {
            "level": "INFO",
            "handlers": ["wsgi"],
        },
    }
)


BANNER = None


def deep_get(structure, path, default=None):
    node = structure
    for part in path.split("."):
        if part.startswith("["):
            index = int(part[1:-1])
            node = node[index]
        elif part in node:
            node = node[part]
        else:
            return default
    return node


@dataclass
class Event:
    project_id: int
    event_id: str

    # envelope_header when the envelope API is used
    envelope_header: Optional[dict] = None
    # item header
    header: Optional[dict] = None
    # item
    # attachments will be stored as bytes, non-attachments as python
    # datastructures
    body: Optional[Union[dict, bytes]] = None

    @property
    def summary(self):
        if not self.body:
            return "no summary"

        if isinstance(self.body, dict):
            # Kent body parsing errors
            kent_error = self.body.get("error")
            if kent_error:
                return kent_error

            # Sentry exceptions events
            exceptions = deep_get(self.body, "exception.values", default=[])
            if exceptions:
                first = exceptions[0]
                return f"{first['type']}: {first['value']}"

            # Sentry message
            msg = deep_get(self.body, "message", default=None)
            if msg:
                return msg

            # CSP security report (older browsers--single report per payload)
            if "csp-report" in self.body:
                directive = deep_get(
                    self.body, "csp-report.violated-directive", default="unknown"
                )
                summary = f"csp-report: {directive}"
                return summary

            if self.body.get("type") == "csp-violation":
                directive = deep_get(
                    self.body, "body.effectiveDirective", default="unknown"
                )
                summary = f"csp-report: {directive}"
                return summary

        return "no summary"

    @property
    def timestamp(self):
        # NOTE(willkg): timestamp is a string
        return self.body.get("timestamp") or str(datetime.datetime.now())

    def to_dict(self):
        return {
            "project_id": self.project_id,
            "event_id": self.event_id,
            "payload": {
                "envelope_header": self.envelope_header,
                "header": self.header,
                "body": self.body,
            },
        }


class EventManager:
    MAX_EVENTS = 100

    def __init__(self):
        # List of Event instances
        self.events = []

    def add_event(
        self, event_id, project_id, envelope_header=None, header=None, body=None
    ):
        event = Event(
            project_id=project_id,
            event_id=event_id,
            envelope_header=envelope_header,
            header=header,
            body=body,
        )
        self.events.append(event)

        while len(self.events) > self.MAX_EVENTS:
            self.events.pop(0)
        return event

    def get_event(self, event_id):
        for event in self.events:
            if event.event_id == event_id:
                return event
        return None

    def get_events(self):
        return self.events

    def flush(self):
        self.events = []


EVENTS = EventManager()


INTERESTING_HEADERS = [
    "User-Agent",
    "X-Sentry-Auth",
]

PROJECTS: dict[int, tuple[str, str]] = {}
projects_file = pathlib.Path.home() / ".kent" / "projects"
if projects_file.exists():
    for line in projects_file.read_text().splitlines():
        """
        Format:
        ```
        <kent_project_id> <local_project_alias> <remote_project_dsn>
        ```
        """
        i, name, real_dsn = line.split(" ")
        PROJECTS[int(i)] = (name, real_dsn)


def notify(event: "Event", event_url: str):
    if has_alerter:
        RELAY_ACTION = "Relay"
        project = str(event.project_id)
        if mapping := PROJECTS.get(event.project_id):
            project = mapping[0]
        # The following blocks until user interacts with notification.
        process = subprocess.run(
            [
                "alerter",
                "-title",
                project,
                "-message",
                str(event.summary),
                "-actions",
                f"{RELAY_ACTION}",
                "-json",
                # Not working :/
                # "-appIcon",
                # "./src/kent/static/favicon.ico"
            ],
            capture_output=True,
        )
        # # BUG(raulparada): alerter returns this on 'show' (available when multiple actions).
        # if process.returncode == 134:
        #     return
        if not process.returncode:
            action = json.loads(process.stdout)
            value = action.get("activationValue")
            if value == None: # Clicked on notification.
                webbrowser.open(event_url)
            elif value == RELAY_ACTION: # Clicked on action.
                print(f"Should relay to {real_dsn=} {event.body=}")
                relay_event(event.event_id)
    else:
        # Fallback, basic notifications.
        process = subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{event.event_id}"'
                f' with title "{event.project_id}"'
                f' subtitle "{event.summary}"',
            ]
        )
    if process.returncode:
        LOGGER.error(
            "Failed sending notification for event %s %s %s",
            event.event_id,
            process.returncode,
            process.stdout,
        )


def relay_event(event_id: str):
    import requests

    event = EVENTS.get_event(event_id)
    assert event, "No event?"
    project = PROJECTS.get(event.project_id)
    if not project:
        error_message = (
            f"Cannot relay event without project mapping for {event.project_id=}"
        )
        LOGGER.error(error_message)
        return error_message, 500

    real_dsn = project[1]
    envelope_url = sentry_dsn_to_envelope_url(real_dsn)
    LOGGER.info("Relaying event id=%s, dsn=%s", event_id, real_dsn)

    relay_response = requests.post(
        envelope_url,
        headers=event.header,
        data=f"{json.dumps(event.envelope_header)}\n{json.dumps(event.header)}\n{json.dumps(event.body)}",
    )
    LOGGER.debug("Relay response %s %s", envelope_url, relay_response.content)
    return relay_response.content, 201


is_darwin = platform.system() == "Darwin"
notifications_enabled = is_darwin and bool(
    int(os.environ.get("KENT_NOTIFICATIONS", "1"))
)
has_alerter = shutil.which("alerter") is not None
if not notifications_enabled:
    LOGGER.warning("Notifications disabled.")
elif not has_alerter:
    LOGGER.info("Get enhanced notification with https://github.com/vjeantet/alerter")


def create_app(test_config=None):
    dev_mode = os.environ.get("KENT_DEV", "0") == "1"

    # Always start an app with an empty error manager
    EVENTS.flush()

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(SECRET_KEY="dev")
    app.wsgi_app = CorsMiddleware(app.wsgi_app)

    if test_config is not None:
        app.config.from_mapping(test_config)

    if BANNER:
        app.logger.info(BANNER)

    if dev_mode:
        logging.getLogger("kent").setLevel(logging.DEBUG)
        app.logger.debug("Dev mode on.")

    @app.route("/", methods=["GET"])
    def index_view():
        host = request.scheme + "://" + request.headers["host"]
        dsn = request.scheme + "://public@" + request.headers["host"] + "/1"

        return render_template(
            "index.html",
            host=host,
            dsn=dsn,
            events=EVENTS.get_events(),
            version=__version__,
        )

    @app.route("/api/event/<event_id>", methods=["GET"])
    def api_event_view(event_id):
        app.logger.info(f"GET /api/event/{event_id}")
        event = EVENTS.get_event(event_id)
        if event is None:
            return {"error": f"Event {event_id} not found"}, 404

        return event.to_dict()

    @app.route("/api/event/<event_id>/relay", methods=["GET", "POST"])
    def api_event_relay(event_id):
        # NOTE: This allows an inappropriate GET for the convenience of triggering from browser.
        app.logger.info(f"GET /api/event/{event_id}/relay")
        event = EVENTS.get_event(event_id)
        if event is None:
            return {"error": f"Event {event_id} not found"}, 404
        return relay_event(event_id)

    @app.route("/api/eventlist/", methods=["GET"])
    def api_event_list_view():
        app.logger.info("GET /api/eventlist/")
        event_ids = [
            {
                "project_id": event.project_id,
                "event_id": event.event_id,
                "summary": event.summary,
            }
            for event in EVENTS.get_events()
        ]
        return {"events": event_ids}

    @app.route("/api/flush/", methods=["POST"])
    def api_flush_view():
        app.logger.info("POST /api/flush")
        EVENTS.flush()
        return {"success": True}

    def log_headers(dev_mode, error_id, headers):
        # Log headers
        if dev_mode:
            for key, val in headers.items():
                app.logger.info("%s: header: %s: %s", error_id, key, val)
        else:
            for key in INTERESTING_HEADERS:
                if key in headers:
                    app.logger.info(
                        "%s: header: %s: %s", error_id, key, request.headers[key]
                    )

    @app.route("/api/<int:project_id>/store/", methods=["POST"])
    def store_view(project_id):
        app.logger.info(f"POST /api/{project_id}/store/")
        event_id = str(uuid.uuid4())
        log_headers(dev_mode, event_id, request.headers)

        # Decompress it
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(request.data)
        elif request.headers.get("content-encoding") == "deflate":
            body = zlib.decompress(request.data)
        else:
            body = request.data

        app.logger.debug(f"{body}")

        # JSON decode payload
        try:
            json_body = json.loads(body)
        except Exception:
            app.logger.exception("%s: exception when JSON-decoding body.", event_id)
            app.logger.error("%s: %s", event_id, json_body)
            EVENTS.add_event(
                event_id=event_id,
                project_id=project_id,
                body={"error": "Kent could not decode body; see logs"},
            )
            raise

        event = EVENTS.add_event(
            event_id=event_id, project_id=project_id, body=json_body
        )

        # Log sentry sdk information from payload
        app.logger.info(
            "%s: sdk: %s %s",
            event_id,
            deep_get(json_body, "sdk.name"),
            deep_get(json_body, "sdk.version"),
        )

        # Log event summary
        app.logger.info("%s: summary: %s", event_id, event.summary)

        # Log event url
        event_url = f"{request.scheme}://{request.headers['host']}/api/event/{event_id}"
        app.logger.info("%s: project id: %s", event_id, project_id)
        app.logger.info("%s: url: %s", event_id, event_url)

        return {"success": True}

    @app.route("/api/<int:project_id>/envelope/", methods=["POST"])
    def envelope_view(project_id):
        app.logger.info(f"POST /api/{project_id}/envelope/")
        request_id = str(uuid.uuid4())
        log_headers(dev_mode, request_id, request.headers)

        # Decompress it
        if request.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(request.data)
        elif request.headers.get("content-encoding") == "deflate":
            body = zlib.decompress(request.data)
        else:
            body = request.data

        app.logger.debug(f"{body}")

        for item in parse_envelope(body):
            event_id = str(uuid.uuid4())
            event = EVENTS.add_event(
                event_id=event_id,
                project_id=project_id,
                envelope_header=item.envelope_header,
                header=item.header,
                body=item.body,
            )

            # Log sentry sdk information from payload
            app.logger.info(
                "%s: sdk: %s %s",
                event_id,
                deep_get(item.body, "sdk.name"),
                deep_get(item.body, "sdk.version"),
            )

            # Log event summary
            app.logger.info("%s: summary: %s", event_id, event.summary)

            # Log event url
            event_url = (
                f"{request.scheme}://{request.headers['host']}/api/event/{event_id}"
            )
            app.logger.info("%s: project id: %s", event_id, project_id)
            app.logger.info("%s: url: %s", event_id, event_url)

            # Notify listeners.
            if notifications_enabled:
                notify_thread = threading.Thread(target=notify, args=(event, event_url))
                notify_thread.start()

        return {"success": True}

    @app.route("/api/<int:project_id>/security/", methods=["POST"])
    def security_view(project_id):
        app.logger.info(f"POST /api/{project_id}/security/")
        event_id = str(uuid.uuid4())
        log_headers(dev_mode, event_id, request.headers)

        body = request.data

        app.logger.debug(f"{body}")

        # Decode the JSON payload
        try:
            json_body = json.loads(body)
        except Exception:
            app.logger.exception("%s: exception when JSON-decoding body.", event_id)
            app.logger.error("%s: %s", event_id, body)
            EVENTS.add_event(
                event_id=event_id,
                project_id=project_id,
                body={"error": "Kent could not decode body; see logs"},
            )
            raise

        if isinstance(json_body, list):
            # Single payload with multiple reports per CSP 3
            for csp_report in json_body:
                event = EVENTS.add_event(
                    event_id=event_id, project_id=project_id, body=csp_report
                )

                # Log event summary
                app.logger.info("%s: summary: %s", event_id, event.summary)

                # Log event url
                event_url = (
                    f"{request.scheme}://{request.headers['host']}/api/event/{event_id}"
                )
                app.logger.info("%s: project id: %s", event_id, project_id)
                app.logger.info("%s: url: %s", event_id, event_url)

        else:
            # Old CSP report format where it's a single report
            event = EVENTS.add_event(
                event_id=event_id, project_id=project_id, body=json_body
            )

            # Log event summary
            app.logger.info("%s: summary: %s", event_id, event.summary)

            # Log event url
            event_url = (
                f"{request.scheme}://{request.headers['host']}/api/event/{event_id}"
            )
            app.logger.info("%s: project id: %s", event_id, project_id)
            app.logger.info("%s: url: %s", event_id, event_url)

        return {"success": True}

    return app
