from seo_workbook_agent.chat_client import post_chat_message


class _FakeCreateRequest:
    def __init__(self, parent, body):
        self.parent = parent
        self.body = body

    def execute(self):
        return {"name": f"{self.parent}/messages/fake-id"}


class _FakeMessages:
    def __init__(self):
        self.create_calls = []

    def create(self, *, parent, body):
        self.create_calls.append({"parent": parent, "body": body})
        return _FakeCreateRequest(parent, body)


class _FakeSpaces:
    def __init__(self):
        self.messages_resource = _FakeMessages()

    def messages(self):
        return self.messages_resource


class _FakeChatService:
    def __init__(self):
        self.spaces_resource = _FakeSpaces()

    def spaces(self):
        return self.spaces_resource


def test_post_chat_message_sends_plain_text_without_thread():
    service = _FakeChatService()

    result = post_chat_message(service, "spaces/AAAA", "hello there")

    assert result == {"name": "spaces/AAAA/messages/fake-id"}
    call = service.spaces_resource.messages_resource.create_calls[0]
    assert call["parent"] == "spaces/AAAA"
    assert call["body"] == {"text": "hello there"}


def test_post_chat_message_includes_thread_when_given():
    service = _FakeChatService()

    post_chat_message(service, "spaces/AAAA", "hello there", thread_name="spaces/AAAA/threads/BBBB")

    call = service.spaces_resource.messages_resource.create_calls[0]
    assert call["body"] == {"text": "hello there", "thread": {"name": "spaces/AAAA/threads/BBBB"}}
