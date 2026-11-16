"""``python -m agent.adapters.web.openapi``: prints the web API's OpenAPI document, the input
of ``make types`` (openapi-typescript into ``web/src/api/schema.d.ts``, ADR 0010).

The document depends only on the routes and the Pydantic models, so it is built with no
ports: nothing here reads ``.env`` or reaches a model, BigQuery or SQLite."""

import json
from typing import Any, cast

from agent.core.chat import ChatService
from agent.core.views import Views
from agent.ports.tracer import Tracer

from .app import create_app


def openapi_document() -> dict[str, Any]:
    """With the dev identity on, so the picker's route and ``DevUser`` are in the types."""
    unused = cast(Any, None)  # never called: no request is served
    app = create_app(
        cast(ChatService, unused), cast(Views, unused), cast(Tracer, unused), dev_users=list
    )
    return app.openapi()


if __name__ == "__main__":
    print(json.dumps(openapi_document(), indent=2, sort_keys=True))
