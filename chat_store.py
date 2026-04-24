from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChatMessage:
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatSession:
    id: str
    project_name: str
    title: str
    thread_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def new(cls, project_name: str) -> "ChatSession":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return cls(
            id=str(uuid.uuid4()),
            project_name=project_name,
            title="New Chat",
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_message(
        self, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        self.messages.append(
            ChatMessage(role=role, content=content, metadata=metadata or {})
        )
        if self.title == "New Chat" and role == "user":
            self.title = content.strip().splitlines()[0][:48] or "New Chat"
        self.touch()
