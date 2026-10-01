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
        f"{index}. {question['title']}\n\n{answer.strip()}"
        for index, (question, answer) in enumerate(zip(questions, answers), 1)
    )


def repeats_question_controls(segment: dict[str, Any]) -> bool:
    """Match only an entire plain question/option listing, never partial text."""
    questions = normalize_questions(segment.get("metadata", {}).get("questions"))
    if not questions:
        return False
    lines = [line.strip() for line in str(segment.get("text") or "").splitlines() if line.strip()]
    expected = []
    for question in questions:
        expected.append(question["title"].strip())
        expected.extend(option.strip() for option in question["options"])
    # Only option bullets are ignored. Extra prose or differently formatted
    # questions must remain visible alongside the controls.
    option_indices = set()
    offset = 0
    for question in questions:
        option_indices.update(range(offset + 1, offset + 1 + len(question["options"])))
        offset += 1 + len(question["options"])
    actual = [
        line[2:].strip() if index in option_indices and line[:2] in {"- ", "* ", "+ "} else line
        for index, line in enumerate(lines)
    ]
    return actual == expected
