"""Compose ordinary user input from non-blocking agent-message questions."""
from __future__ import annotations

from typing import Any


def normalize_questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    questions = []
    for question in value:
        if not isinstance(question, dict):
            continue
        title = question.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        options = question.get("options")
        questions.append({
            "title": title,
            "options": list(dict.fromkeys(
                option for option in options
                if isinstance(option, str) and option.strip()
            )) if isinstance(options, list) else [],
        })
    return questions


def agent_message_metadata(item: dict[str, Any], thread_id: str = "") -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if item.get("delivery") == "async":
        metadata["delivery"] = "async"
    questions = normalize_questions(item.get("questions"))
    if questions:
        metadata["questions"] = questions
        if thread_id:
            metadata["thread_id"] = thread_id
    return metadata


def answers_prompt(questions: list[dict[str, Any]], answers: list[str]) -> str:
    if not questions or len(answers) != len(questions) or any(
        not answer.strip() for answer in answers
    ):
        raise ValueError("Answer each question before adding to the draft.")
    return "Answers to your questions:\n\n" + "\n\n".join(
        f"{index}. {question['title']}\n{answer.strip()}"
        for index, (question, answer) in enumerate(zip(questions, answers), 1)
    )
