"""
Collaboration module — server-side Yjs CRDT peers.

The legacy ``api_gateway.routers.collab`` was a dumb byte relay that
forwarded y-websocket protocol bytes between browser clients without
understanding them. This module adds a *server-side* peer that
participates in the same Y protocol as humans — same WebSocket relay,
same Y.Doc state, same awareness channel — so an AI agent can apply
SQL edits that humans see typed out in real time, with a 'thinking'/
'composing' presence indicator surfacing in the same cursors UI.
"""
