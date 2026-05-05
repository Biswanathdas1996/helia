from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from app import llm

AgentAction = Literal["ask_clarifying_question", "answer", "offer_ticket", "create_ticket"]
_VALID_ACTIONS: set[str] = {"ask_clarifying_question", "answer", "offer_ticket", "create_ticket"}
_MAX_FACTS = 6
_MAX_CLARIFICATIONS = 2


@dataclass(slots=True)
class AgentDecision:
    action: AgentAction
    reply: str
    summary: str
    known_facts: list[str]
    missing_facts: list[str]
    used_citations: list[int]
    confidence: float | None = None


def default_state() -> dict[str, Any]:
    return {
        "summary": "",
        "knownFacts": [],
        "missingFacts": [],
        "lastAction": None,
        "lastQuestion": None,
        "clarificationCount": 0,
        "resolutionSummary": None,
    }


def normalize_state(raw: Any) -> dict[str, Any]:
    state = default_state()
    if not isinstance(raw, dict):
        return state

    summary = raw.get("summary")
    if isinstance(summary, str):
        state["summary"] = summary.strip()

    resolution = raw.get("resolutionSummary")
    if isinstance(resolution, str) and resolution.strip():
        state["resolutionSummary"] = resolution.strip()

    last_action = raw.get("lastAction")
    if isinstance(last_action, str) and last_action.strip():
        state["lastAction"] = last_action.strip()

    last_question = raw.get("lastQuestion")
    if isinstance(last_question, str) and last_question.strip():
        state["lastQuestion"] = last_question.strip()

    clarification_count = raw.get("clarificationCount")
    if isinstance(clarification_count, (int, float)):
        state["clarificationCount"] = max(0, int(clarification_count))

    state["knownFacts"] = _normalize_string_list(raw.get("knownFacts"), limit=_MAX_FACTS)
    state["missingFacts"] = _normalize_string_list(raw.get("missingFacts"), limit=3)
    return state


def state_context_block(state: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = str(state.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")

    known_facts = _normalize_string_list(state.get("knownFacts"), limit=_MAX_FACTS)
    if known_facts:
        lines.append("Known facts:")
        lines.extend(f"- {item}" for item in known_facts)

    missing_facts = _normalize_string_list(state.get("missingFacts"), limit=3)
    if missing_facts:
        lines.append("Missing facts:")
        lines.extend(f"- {item}" for item in missing_facts)

    last_question = str(state.get("lastQuestion") or "").strip()
    if last_question:
        lines.append(f"Last clarifying question: {last_question}")

    clarification_count = int(state.get("clarificationCount") or 0)
    lines.append(f"Clarifying questions already asked: {clarification_count}")
    return "\n".join(lines) if lines else "(no investigation memory yet)"


def apply_decision_to_state(
    state: dict[str, Any],
    decision: AgentDecision,
    *,
    final_answer: str | None = None,
) -> dict[str, Any]:
    next_state = normalize_state(state)
    next_state["summary"] = decision.summary.strip()
    next_state["knownFacts"] = _merge_fact_lists(
        next_state.get("knownFacts"),
        decision.known_facts,
        limit=_MAX_FACTS,
    )
    next_state["missingFacts"] = _remaining_missing_facts(
        next_state.get("missingFacts"),
        decision.missing_facts,
        next_state["knownFacts"],
    )
    next_state["lastAction"] = decision.action

    if decision.action == "ask_clarifying_question":
        next_state["clarificationCount"] = int(next_state.get("clarificationCount") or 0) + 1
        next_state["lastQuestion"] = decision.reply.strip() or None
        next_state["resolutionSummary"] = None
    elif decision.action == "answer":
        next_state["lastQuestion"] = None
        next_state["missingFacts"] = []
        if final_answer and final_answer.strip():
            next_state["resolutionSummary"] = final_answer.strip()[:500]
    else:
        next_state["lastQuestion"] = None
        next_state["resolutionSummary"] = None

    return next_state


async def decide_next_action(
    *,
    recent_messages: list[dict[str, Any]],
    current_user_message: str,
    retrieval_context: str,
    citations: list[dict[str, Any]],
    memory_snippets: list[str],
    agent_state: dict[str, Any],
) -> AgentDecision:
    normalized_state = normalize_state(agent_state)
    system_prompt = (
        "You are Helia's support investigation planner. Decide the single best next action in a"
        " customer support conversation.\n\n"
        "Available actions:\n"
        "- ask_clarifying_question\n"
        "- answer\n"
        "- offer_ticket\n"
        "- create_ticket\n\n"
        "Decision policy:\n"
        "- Behave like a careful human support agent: investigate briefly, accumulate the user's"
        " answers as confirmed facts, and move toward a useful solution instead of restarting the"
        " diagnosis each turn.\n"
        "- On the first assistant turn of a new investigation, do not jump straight to the solution."
        " Ask one targeted clarifying question first, using working memory, durable memory, and"
        " retrieved knowledge to choose the highest-value missing fact.\n"
        "- After the user answers that first question, continue with the smallest number of additional"
        " targeted questions needed to safely narrow the diagnosis, then give the grounded next step.\n"
        "- Prefer answer when the knowledge evidence is specific enough to help the user now and the"
        " first-turn clarification requirement has already been satisfied.\n"
        "- Use ask_clarifying_question only when one missing fact would materially change the answer"
        " or the document you would rely on.\n"
        "- Ask exactly one concise, polite, highly targeted question. Never ask generic questions"
        " like 'can you provide more details?'.\n"
        "- Base the clarifying question on the retrieved knowledge when possible: ask about product,"
        " plan, environment, version, error code, or the exact step that failed only if that"
        " distinction matters to the evidence.\n"
        "- Do not ask for facts already present in the working memory or recent messages.\n"
        "- Update knownFacts with durable facts confirmed by the user. Keep previously confirmed facts"
        " unless they are clearly contradicted.\n"
        "- Keep missingFacts short and concrete. Remove a missing fact once the user has answered it.\n"
        "- If the current message answers the last clarifying question, prefer either one final high-value"
        " follow-up or a grounded answer, not another broad investigation restart.\n"
        "- Do not push escalation or ticket creation unless the user explicitly asks for a human"
        " handoff or support ticket.\n"
        "- If two clarifying questions have already been asked, prefer giving the best grounded next"
        " step you can, and clearly state the single most important missing detail rather than"
        " defaulting to escalation.\n"
        "- If the user clearly wants a human handoff, choose offer_ticket.\n"
        "- Choose create_ticket only when the user has explicitly consented to creating a support"
        " ticket in this conversation.\n"
        "- Keep a calm, human support tone.\n"
        "- When writing a clarifying question or handoff reply, sound emotionally aware and natural:"
        " acknowledge the user's situation, show empathy or sympathy when they are blocked or"
        " frustrated, show appreciation when they already shared useful detail, and use light positive"
        " warmth or joy only when the context genuinely supports it.\n"
        "- Do not overdo emotion. Keep it believable, brief, and appropriate to the user's situation."
        " If the user reports a failure, issue, or loss, use empathy or sorrowful concern rather than"
        " cheerful wording.\n\n"
        "Example:\n"
        "User says they get access denied on a Jira board. If the knowledge depends on whether the"
        " user can open other Jira projects or whether the issue is only one board, ask that specific"
        " question first. After the user answers, use that fact in knownFacts and give the most likely"
        " permission or project-role fix from the knowledge base.\n\n"
        "Return JSON with this exact shape:\n"
        '{"action":"ask_clarifying_question|answer|offer_ticket|create_ticket",'
        '"reply":"assistant text for ask_clarifying_question, offer_ticket, or create_ticket; empty string for answer",'
        '"summary":"1-2 sentence investigation summary",'
        '"knownFacts":["fact"],'
        '"missingFacts":["fact"],'
        '"usedCitations":[1],'
        '"confidence":0.0}'
    )
    user_prompt = (
        f"Conversation working memory:\n{state_context_block(normalized_state)}\n\n"
        f"Durable user memory:\n{_memory_block(memory_snippets)}\n\n"
        f"Recent conversation:\n{_history_block(recent_messages)}\n\n"
        f"Latest user message:\n{current_user_message.strip()}\n\n"
        f"Retrieved knowledge snippets:\n{_citations_block(citations)}\n\n"
        f"Retrieved context block:\n{retrieval_context or '(no retrieved context)'}\n"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.1,
            max_tokens=700,
            reasoning=True,
        )
        parsed = _parse_json_object(raw)
    except Exception:
        parsed = None

    decision = _coerce_decision(parsed, normalized_state, citations)
    if _should_force_first_clarification(normalized_state, recent_messages, decision):
        return AgentDecision(
            action="ask_clarifying_question",
            reply=_build_first_clarifying_question(decision.missing_facts),
            summary=(
                decision.summary
                or "The issue needs one targeted clarification before a grounded answer is safe."
            ),
            known_facts=decision.known_facts,
            missing_facts=decision.missing_facts,
            used_citations=decision.used_citations,
            confidence=decision.confidence,
        )
    if (
        decision.action == "ask_clarifying_question"
        and int(normalized_state.get("clarificationCount") or 0) >= _MAX_CLARIFICATIONS
    ):
        return AgentDecision(
            action="answer",
            reply="",
            summary=decision.summary,
            known_facts=decision.known_facts,
            missing_facts=decision.missing_facts,
            used_citations=decision.used_citations,
            confidence=decision.confidence,
        )
    return decision


def _should_force_first_clarification(
    state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    decision: AgentDecision,
) -> bool:
    if decision.action != "answer":
        return False
    if int(state.get("clarificationCount") or 0) > 0:
        return False
    if _normalize_string_list(state.get("knownFacts"), limit=_MAX_FACTS):
        return False
    if str(state.get("lastQuestion") or "").strip():
        return False
    if str(state.get("resolutionSummary") or "").strip():
        return False
    return not any(str(message.get("role") or "").strip().lower() == "assistant" for message in recent_messages)


def _build_first_clarifying_question(missing_facts: list[str]) -> str:
    fact = next((item.strip() for item in missing_facts if item and item.strip()), "")
    if fact:
        normalized = " ".join(fact.split()).rstrip(".")
        lowered = normalized[:1].lower() + normalized[1:] if normalized else normalized
        if lowered.startswith(("whether ", "which ", "what ", "when ", "where ", "who ", "why ", "how ", "if ")):
            return f"Before I suggest a fix, could you confirm {lowered}?"
        return f"Before I suggest a fix, could you share {lowered}?"
    return (
        "Before I suggest a fix, could you share the exact product area, the step that fails,"
        " and the exact error wording you see?"
    )


def _coerce_decision(
    parsed: dict[str, Any] | None,
    state: dict[str, Any],
    citations: list[dict[str, Any]],
) -> AgentDecision:
    summary = str((parsed or {}).get("summary") or state.get("summary") or "").strip()
    known_facts = _normalize_string_list((parsed or {}).get("knownFacts"), limit=_MAX_FACTS)
    if not known_facts:
        known_facts = _normalize_string_list(state.get("knownFacts"), limit=_MAX_FACTS)
    missing_facts = _normalize_string_list((parsed or {}).get("missingFacts"), limit=3)
    used_citations = _normalize_int_list((parsed or {}).get("usedCitations"), limit=max(1, len(citations)))
    confidence = _coerce_float((parsed or {}).get("confidence"))

    raw_action = str((parsed or {}).get("action") or "").strip()
    action: AgentAction = "answer"
    if raw_action in _VALID_ACTIONS:
        action = raw_action  # type: ignore[assignment]

    reply = str((parsed or {}).get("reply") or "").strip()
    if action == "ask_clarifying_question" and not reply:
        reply = "Could you share the exact product area and the step where this is failing?"
    if action == "offer_ticket" and not reply:
        reply = (
            "If you want, I can help you escalate this to a human teammate."
        )
    if action == "create_ticket" and not reply:
        reply = "Thanks for confirming. I will create a support ticket with this investigation summary now."

    if action == "answer":
        reply = ""
    if action == "answer" and not summary:
        summary = "The issue appears specific enough to answer from the current verified knowledge."
    if action != "answer" and not summary:
        summary = "The issue still needs a bit more investigation before a final answer is safe."

    return AgentDecision(
        action=action,
        reply=reply,
        summary=summary,
        known_facts=known_facts,
        missing_facts=missing_facts,
        used_citations=used_citations,
        confidence=confidence,
    )


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if 0 <= first_brace < last_brace:
        candidates.append(text[first_brace : last_brace + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _history_block(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "(no prior conversation)"
    lines: list[str] = []
    for message in messages[-8:]:
        role = str(message.get("role") or "user")
        content = " ".join(str(message.get("content") or "").split())
        if not content:
            continue
        lines.append(f"{role}: {content[:400]}")
    return "\n".join(lines) or "(no prior conversation)"


def _memory_block(memory_snippets: list[str]) -> str:
    items = [item.strip() for item in memory_snippets if item and item.strip()]
    if not items:
        return "(none)"
    return "\n".join(f"- {item}" for item in items[:5])


def _citations_block(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "(none)"
    lines: list[str] = []
    for idx, citation in enumerate(citations[:5], start=1):
        document_name = str(citation.get("documentName") or "Untitled")
        snippet = " ".join(str(citation.get("snippet") or "").split())[:320]
        lines.append(f"[{idx}] {document_name}: {snippet}")
    return "\n".join(lines)


def _normalize_string_list(raw: Any, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in out:
            continue
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _normalize_int_list(raw: Any, *, limit: int) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            value = item
        elif isinstance(item, float):
            value = int(item)
        elif isinstance(item, str):
            try:
                value = int(item)
            except ValueError:
                continue
        else:
            continue
        if value <= 0 or value in out:
            continue
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value)))
        except ValueError:
            return None
    return None


def _merge_fact_lists(existing: Any, incoming: list[str], *, limit: int) -> list[str]:
    out = _normalize_string_list(existing, limit=limit)
    for item in incoming:
        value = item.strip()
        if not value:
            continue
        if value in out:
            continue
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _remaining_missing_facts(existing: Any, incoming: list[str], known_facts: list[str]) -> list[str]:
    combined = _merge_fact_lists(existing, incoming, limit=3)
    known_normalized = {item.strip().lower() for item in known_facts if item.strip()}
    out: list[str] = []
    for item in combined:
        lowered = item.strip().lower()
        if lowered and lowered in known_normalized:
            continue
        if item in out:
            continue
        out.append(item)
        if len(out) >= 3:
            break
    return out