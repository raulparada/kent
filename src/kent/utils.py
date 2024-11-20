# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from dataclasses import dataclass
import logging
import json
from typing import Union


from werkzeug.wrappers import Request
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)


@dataclass
class Item:
    envelope_header: dict
    header: dict
    body: Union[dict, bytes]


def get_newline_index(body, start_index, end_index):
    end_index = body.find(b"\n", start_index)
    if end_index == -1:
        # If there are no more \n, then the end_index is the last index in the
        # body
        end_index = len(body)
    else:
        while body[end_index] == "\r":
            end_index = body.find(b"\n", end_index + 1)
    return end_index


def parse_envelope(body):
    """Parses an envelope payload into items

    :arg body: the envelope payload body

    :returns: generator of items

    """

    body_length = len(body)
    start_index = end_index = 0
    read_length = -1

    envelope_header = None

    # Absorb envelope
    # See: https://develop.sentry.dev/sdk/envelopes/
    while end_index < body_length:
        start_index = end_index
        end_index = get_newline_index(body, start_index, end_index)

        if envelope_header is None:
            envelope_header = json.loads(body[start_index:end_index])
            end_index += 1
            continue

        json_part = body[start_index:end_index]

        try:
            part = json.loads(json_part)
        except Exception:
            LOGGER.exception("exception when JSON-decoding body.")
            LOGGER.error("%s", json_part)
            raise

        if "type" in part:
            # Advance past the \n
            end_index += 1

            start_index = end_index
            read_length = part.get("length", -1)
            if read_length != -1:
                # NOTE(willkg): This will include the newline separater at the end
                end_index = end_index + read_length
            else:
                end_index = get_newline_index(body, start_index, end_index)

            # NOTE(willkg): This drops the newline separator because it's not
            # part of the Item body
            item_body = body[start_index:end_index]

            if part.get("type") == "attachment":
                yield Item(
                    envelope_header=envelope_header,
                    header=part,
                    body=item_body,
                )

            else:
                item_body_data = json.loads(item_body)
                yield Item(
                    envelope_header=envelope_header, header=part, body=item_body_data
                )

            # Advance past the \n
            end_index += 1
            continue


class CorsMiddleware:
    """
    Minimal, allow-all CORS middleware.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        def cors_response(status: str, response_headers: list, exc_info=None):
            request = Request(environ)
            if request.method == "OPTIONS":
                response_headers.append(("Access-Control-Allow-Origin", "*"))
                response_headers.append(("Access-Control-Allow-Headers", "*"))
                response_headers.append(("Access-Control-Allow-Methods", "*"))
            else:
                response_headers.append(("Access-Control-Allow-Origin", "*"))
            return start_response(status, response_headers, exc_info)

        return self.app(environ, cors_response)


def sentry_dsn_to_envelope_url(dsn):
    parsed = urlparse(dsn)
    host = parsed.hostname
    if port := parsed.port:
        host += ":" + str(port)
    return f"{parsed.scheme}://{host}/api/{parsed.path.lstrip('/')}/envelope/?sentry_key={parsed.username}"
