class StubAgentCore:
    """Drop-in replacement for AgentCore in router tests — avoids needing a
    real model/MCP connection just to test HTTP-layer auth and wiring.
    """

    def __init__(self, reply: str = "stub reply") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, str]] = []

    async def handle_turn(self, *, user_id: str, session_id: str, message: str) -> str:
        self.calls.append((user_id, session_id, message))
        return self.reply
