"""ChannelAdapter: where one turn's outbound messages go (CLI, web, Slack, Teams).

Channels have two sides, and only one of them is this port:

- **Inbound (driving side).** The channel's own loop or request handler calls
  ``agent.core.chat.ChatService.handle(message, channel)``: the CLI's read loop, the web
  adapter's ``POST /api/messages`` handler (ADR 0010). Nothing pulls from a channel.
- **Outbound (driven side), this port.** ``handle`` wraps the ``channel`` it was given in
  ``agent.core.outbound.Outbound`` for that turn, so every message reaches ``deliver`` only
  after the output PII filter.

A ``ChannelAdapter`` is therefore per turn or per connection (stdout for the CLI, one SSE
stream per request for the web), not a process-wide singleton.
"""

from typing import Protocol

from agent.contracts.channel import OutboundMessage


class ChannelAdapter(Protocol):
    """Interface:

    - ``deliver`` renders one outbound message in the channel's own format: text in the CLI,
      an SSE event for the web, checkboxes and buttons for a ``ConfirmationRequest`` in Slack,
      Teams and web.
    - **Only ``agent.core.outbound.Outbound`` may call it.** Whatever it receives has passed
      the output PII filter.
    - Never raises to the graph: a channel failure (for example, the browser closed the stream)
      is traced, and the turn's state is already checkpointed.
    """

    def deliver(self, message: OutboundMessage) -> None: ...
