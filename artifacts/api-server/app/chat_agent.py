from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from app import llm

AgentAction = Literal["ask_clarifying_question", "answer", "offer_ticket", "create_ticket"]
_VALID_ACTIONS: set[str] = {"ask_clarifying_question", "answer", "offer_ticket", "create_ticket"}
_MAX_FACTS = 6
_MIN_CLARIFICATIONS = 3
_MAX_CLARIFICATIONS = 3
_TICKET_OFFER_THRESHOLD = 2


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
        "solutionAttempts": 0,
        "lastUnresolvedSignal": None,
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

    solution_attempts = raw.get("solutionAttempts")
    if isinstance(solution_attempts, (int, float)):
        state["solutionAttempts"] = max(0, int(solution_attempts))

    last_unresolved = raw.get("lastUnresolvedSignal")
    if isinstance(last_unresolved, str) and last_unresolved.strip():
        state["lastUnresolvedSignal"] = last_unresolved.strip()

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

    solution_attempts = int(state.get("solutionAttempts") or 0)
    lines.append(f"Solution attempts already given: {solution_attempts}")

    last_unresolved = str(state.get("lastUnresolvedSignal") or "").strip()
    if last_unresolved:
        lines.append(f"User's latest unresolved signal: {last_unresolved}")
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
            next_state["solutionAttempts"] = int(next_state.get("solutionAttempts") or 0) + 1
    else:
        next_state["lastQuestion"] = None
        next_state["resolutionSummary"] = None

    next_state["lastUnresolvedSignal"] = None
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
        "- Behave like a careful, friendly human support teammate. Have a real back-and-forth: ask,"
        " listen, accumulate the user's answers as confirmed facts, and move toward a useful fix"
        " instead of restarting the diagnosis each turn.\n"
        "- The investigation MUST follow this exact sequence, one step per assistant turn:\n"
        "  1. Clarifying question 1 (grounded in retrieved knowledge snippets).\n"
        "  2. Clarifying question 2 (grounded, on a different distinguishing dimension).\n"
        "  3. Clarifying question 3 (grounded, on yet another distinguishing dimension).\n"
        "  4. Probable explanation of the likely cause(s), grounded in the snippets — this is the"
        "     'answer' action, Stage 1.\n"
        "  5. A concrete 'try this' step from the snippets — answer action, Stage 2.\n"
        "  6. Confirmation prompt asking what happened — answer action, Stage 3.\n"
        "  7. Final grounded resolution OR offer_ticket if it still did not work — Stage 4.\n"
        "- Until clarificationCount is at least 3, you MUST choose ask_clarifying_question (unless the"
        " user has explicitly asked for a human/ticket, in which case choose offer_ticket). Do not"
        " skip ahead to answer just because you think you already know the cause — three grounded"
        " clarifications are required to safely narrow the diagnosis.\n"
        "- Each of the three clarifying questions must probe a DIFFERENT distinguishing dimension that"
        " the retrieved snippets actually separate on (for example: which product/app, which version"
        " or platform, which exact step or error wording, which environment, which device or audio"
        " path, etc.). Do not repeat or rephrase a previous question.\n"
        "- Ground every clarifying question in the retrieved knowledge snippets: ask about a product,"
        " plan, environment, version, error code, setting, or step that the snippets actually"
        " distinguish between. Do not ask about product features or UI elements that are not present"
        " in the snippets. If the snippets are too thin to support a third grounded question, choose"
        " offer_ticket instead of inventing one.\n"
        "- Ask exactly one concise, polite, highly targeted question per turn. Never ask generic"
        " questions like 'can you provide more details?'.\n"
        "- Do not ask for facts already present in the working memory or recent messages.\n"
        "- Update knownFacts with durable facts confirmed by the user. Keep previously confirmed facts"
        " unless they are clearly contradicted.\n"
        "- Keep missingFacts short and concrete. Remove a missing fact once the user has answered it.\n"
        "- Once clarificationCount has reached 3, choose answer so the downstream stage flow can"
        " produce the probable explanation (Stage 1), then a try-this step (Stage 2), then a"
        " confirmation prompt (Stage 3), and finally the resolution (Stage 4).\n"
        "- The downstream answer step is forbidden from inventing UI steps or product details from"
        " general knowledge. If after three clarifications the snippets still contain no concrete"
        " grounded action that fits the user's situation, choose offer_ticket instead of answer.\n"
        "- Never ask a fourth clarifying question. After three, move to answer or offer_ticket.\n\n"
        "Multi-attempt resolution loop (very important):\n"
        "- Track solutionAttempts (the number of grounded fixes already given in this conversation)"
        " and lastUnresolvedSignal (a phrase from the user indicating the previous fix did not work).\n"
        "- If lastUnresolvedSignal is set and solutionAttempts is 1, choose answer and propose a"
        " different angle from the knowledge base. Briefly acknowledge that the first try did not work"
        " before offering the new step.\n"
        "- If solutionAttempts is 2 or more and lastUnresolvedSignal is set, choose offer_ticket."
        " Apologize warmly that the earlier steps did not resolve it, thank the user for trying, and"
        " offer to open a support ticket so a human teammate can pick it up. Ask consent; do not assume.\n"
        "- If the user clearly asks for a human, an agent, or a ticket, choose offer_ticket immediately"
        " regardless of solutionAttempts.\n"
        "- Choose create_ticket only when the user has explicitly consented in this conversation"
        " (for example: 'yes, create a ticket', 'go ahead', 'please open one').\n\n"
        "Tone and length:\n"
        "- Keep every reply short and conversational: 2 to 3 short sentences. No long bullet lists in"
        " ask_clarifying_question or offer_ticket replies. Sound like a teammate, not a help article.\n"
        "- Show emotional intelligence: acknowledge the user's situation, show empathy when they are"
        " blocked or frustrated, appreciate the detail they have already shared, and use light warmth"
        " only when the context genuinely supports it. Never sound cheerful about a failure or denial.\n"
        "- Keep emotion believable, brief, and appropriate. If the user reports loss, failure, or"
        " inconvenience, lean into gentle concern rather than upbeat wording.\n\n"
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
    clarification_count = int(normalized_state.get("clarificationCount") or 0)
    if _should_force_clarification(normalized_state, recent_messages, decision):
        forced_reply = decision.reply.strip() if decision.action == "ask_clarifying_question" else ""
        if not forced_reply:
            forced_reply = _build_grounded_clarifying_question(
                decision.missing_facts,
                citations,
                clarification_count,
            )
        return AgentDecision(
            action="ask_clarifying_question",
            reply=forced_reply,
            summary=(
                decision.summary
                or "The issue still needs grounded clarifications before a safe answer is possible."
            ),
            known_facts=decision.known_facts,
            missing_facts=decision.missing_facts,
            used_citations=decision.used_citations,
            confidence=decision.confidence,
        )
    if (
        decision.action == "ask_clarifying_question"
        and clarification_count >= _MAX_CLARIFICATIONS
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


def _should_force_clarification(
    state: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    decision: AgentDecision,
) -> bool:
    if decision.action in ("offer_ticket", "create_ticket"):
        return False
    clarification_count = int(state.get("clarificationCount") or 0)
    if clarification_count >= _MIN_CLARIFICATIONS:
        return False
    if str(state.get("resolutionSummary") or "").strip():
        return False
    if str(state.get("lastUnresolvedSignal") or "").strip():
        return False
    if decision.action == "ask_clarifying_question":
        return False
    return True


_CLARIFICATION_FALLBACKS: tuple[str, ...] = (
    "Happy to help — to point you at the right guidance, could you share which product or app this is happening in?",
    "Thanks for that. Could you tell me the exact step or screen where the issue shows up, and the wording of any error you see?",
    "Got it. One last thing before I share what's likely going on — which device, version, or environment are you on (for example desktop app vs web, OS, or version number)?",
)


def _build_grounded_clarifying_question(
    missing_facts: list[str],
    citations: list[dict[str, Any]],
    clarification_count: int,
) -> str:
    fact_index = max(0, clarification_count)
    facts = [item.strip() for item in missing_facts if item and item.strip()]
    if fact_index < len(facts):
        normalized = " ".join(facts[fact_index].split()).rstrip(".")
        if normalized:
            lowered = normalized[:1].lower() + normalized[1:]
            if lowered.startswith(("whether ", "which ", "what ", "when ", "where ", "who ", "why ", "how ", "if ")):
                return f"Thanks — could you confirm {lowered}?"
            return f"Thanks — could you share {lowered}?"
    fallback_index = min(fact_index, len(_CLARIFICATION_FALLBACKS) - 1)
    return _CLARIFICATION_FALLBACKS[fallback_index]


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
            "I'm sorry the earlier steps haven't sorted this. If you'd like, I can open a support "
            "ticket so a human teammate can pick it up — just say the word and I'll create it."
        )
    if action == "create_ticket" and not reply:
        reply = "Thanks for confirming — creating a support ticket now with a summary of what we've tried."

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


_ACTION_VERB_PATTERN = re.compile(
    r"\b(click|open|select|navigate|go to|tap|press|choose|enable|disable|toggle|"
    r"set|configure|run|launch|enter|type|paste|switch|check|uncheck|hover|drag|"
    r"scroll|expand|collapse|right-click|double-click)\b",
    re.IGNORECASE,
)


def has_action_claims(text: str) -> bool:
    return bool(_ACTION_VERB_PATTERN.search(text or ""))


async def verify_answer_grounding(
    *,
    answer: str,
    citations: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Return (is_grounded, unsupported_phrases).

    Uses a strict LLM verifier to confirm every UI step, menu name, button
    label, settings path, command, error code, and product-specific claim in
    the answer is directly supported by the cited snippets. Fail-open on
    infrastructure errors (returns grounded=True with empty list) so that
    transient verifier failures do not silently block real answers.
    """
    text = (answer or "").strip()
    if not text:
        return True, []
    if not citations:
        return (not has_action_claims(text)), (
            ["answer contains action steps but there are no cited snippets"]
            if has_action_claims(text) else []
        )

    snippets_block = _citations_block(citations)
    system_prompt = (
        "You verify grounding for a support assistant. "
        "Check whether each concrete claim in the assistant answer is directly supported by the "
        "numbered snippets.\n\n"
        "Mark a claim unsupported ONLY if the answer names a specific UI label, menu/settings path, "
        "command, shortcut, URL, file path, config value, or error code that is NOT present in any "
        "snippet. Do not flag generic recommendations, plain-language paraphrases, or commonly-known "
        "concepts.\n\n"
        "Rules:\n"
        "- Minor wording, casing, and punctuation differences are OK when the same thing is clearly meant.\n"
        "- Do not allow INVENTED specific UI labels (e.g. 'click Settings > Audio > Suppress Background Noise') "
        "unless those exact strings appear in a snippet.\n"
        "- Ignore generic polite language and conversational acknowledgements.\n"
        "- Narrative / ticket / incident snippets: many snippets are user-written narratives or ticket "
        "history (e.g. 'switched audio device, echo stopped'; 'unplugged headset and used speakers'). "
        "Treat these as narrative-form instructions. A plain-language recommendation that mirrors the "
        "narrative action (e.g. 'try switching your audio device', 'try a different microphone', 'try "
        "without your headset', 'lower your speaker volume', 'enable echo cancellation') IS grounded "
        "even if the snippet does not phrase it as a step. Only flag if the recommendation invents a "
        "specific UI control or menu path the snippet did not describe.\n"
        "- Probable-cause statements ('this is often caused by…', 'echo usually comes from…') ARE "
        "grounded when the snippets describe similar incidents or fixes — diagnostic framing of the "
        "same scenario the snippets cover counts as supported, not invented.\n"
        "- When in doubt, prefer grounded=true. Only return grounded=false when there is a clearly "
        "fabricated specific label, command, or path.\n\n"
        "Return strict JSON only:\n"
        '{"grounded": boolean, "unsupported": ["short verbatim unsupported phrase", ...]}'
    )
    user_prompt = (
        f"Numbered snippets:\n{snippets_block}\n\n"
        f"Assistant answer to verify:\n{text}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=300,
        )
        parsed = _parse_json_object(raw)
    except Exception:
        return True, []

    if not isinstance(parsed, dict):
        return True, []

    grounded = parsed.get("grounded")
    if not isinstance(grounded, bool):
        return True, []

    unsupported_raw = parsed.get("unsupported")
    unsupported: list[str] = []
    if isinstance(unsupported_raw, list):
        for item in unsupported_raw:
            if isinstance(item, str) and item.strip():
                unsupported.append(item.strip()[:200])
            if len(unsupported) >= 5:
                break

    return grounded, unsupported


async def verify_answer_on_topic(
    *,
    user_query: str,
    answer: str,
    citations: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Return (is_on_topic, off_topic_phrases).

    Checks that the answer addresses the same product/feature/scope as the
    consolidated user query, and does not introduce a different product or
    feature the user did not ask about. This catches drift like "user asked
    about Zoom but the answer talks about Bluetooth" — a class of error the
    UI-grounding verifier in :func:`verify_answer_grounding` does not catch
    because each individual UI label may still appear in some snippet.

    Fail-open on infrastructure errors so transient verifier failures do not
    silently block real answers.
    """
    query = (user_query or "").strip()
    text = (answer or "").strip()
    if not text or not query:
        return True, []

    snippets_block = _citations_block(citations)
    system_prompt = (
        "You verify that a support assistant's answer stays on the topic the user actually asked"
        " about. The user query has already been consolidated into a self-contained question that"
        " names the product, feature, and scope they care about.\n\n"
        "Mark the answer off-topic ONLY if it introduces a DIFFERENT product, feature, or scope"
        " that the user did not ask about. Examples of off-topic drift:\n"
        "- User asked about Zoom audio; answer recommends Bluetooth pairing or Windows Bluetooth"
        " settings.\n"
        "- User asked about Jira project access; answer talks about Confluence permissions.\n"
        "- User asked how to enable a feature; answer recommends uninstalling the app instead.\n\n"
        "An answer is ON-TOPIC if it addresses the same product / feature area as the query, even"
        " if it cites a snippet that also mentions adjacent topics. Do NOT flag warm openers,"
        " polite acknowledgements, generic guidance, or follow-up questions. Do NOT flag a citation"
        " just because the snippet covers more than the user asked — only flag the assistant's own"
        " RECOMMENDATIONS that drift to a different product / feature.\n\n"
        "When in doubt, prefer onTopic=true. Only return onTopic=false when the answer's"
        " recommendation is clearly about a different product or feature than the consolidated"
        " query.\n\n"
        "Return strict JSON only:\n"
        '{"onTopic": boolean, "offTopic": ["short verbatim drifting phrase", ...]}'
    )
    user_prompt = (
        f"Consolidated user query:\n{query}\n\n"
        f"Numbered snippets the answer was grounded against:\n{snippets_block}\n\n"
        f"Assistant answer to verify:\n{text}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=300,
        )
        parsed = _parse_json_object(raw)
    except Exception:
        return True, []

    if not isinstance(parsed, dict):
        return True, []

    on_topic = parsed.get("onTopic")
    if not isinstance(on_topic, bool):
        return True, []

    off_topic_raw = parsed.get("offTopic")
    off_topic: list[str] = []
    if isinstance(off_topic_raw, list):
        for item in off_topic_raw:
            if isinstance(item, str) and item.strip():
                off_topic.append(item.strip()[:200])
            if len(off_topic) >= 5:
                break
    return on_topic, off_topic


async def rewrite_to_topic(
    *,
    user_query: str,
    answer: str,
    citations: list[dict[str, Any]],
    off_topic: list[str],
) -> str | None:
    """Ask the LLM to rewrite the answer so it addresses only the consolidated
    user query, removing any drift to a different product/feature. Returns the
    rewritten text, or ``None`` if the answer cannot be salvaged on topic.
    """
    query = (user_query or "").strip()
    text = (answer or "").strip()
    if not text or not query or not citations:
        return None

    snippets_block = _citations_block(citations)
    flagged = "\n".join(f"- {item}" for item in off_topic[:5]) or "- (verifier did not list specific phrases)"

    system_prompt = (
        "You repair a support assistant answer that drifted off the user's actual question.\n"
        "- The consolidated user query is the authoritative statement of what the user asked.\n"
        "- Rewrite the answer so it stays strictly on the product, feature, and scope named in"
        " that query. Remove any recommendation, cause, or step that is about a different"
        " product or feature, even if a snippet mentioned it.\n"
        "- Use ONLY the numbered snippets, and only the parts of those snippets that are about the"
        " product/feature in the user's query. Cite snippets inline as [n].\n"
        "- Preserve the warm, brief, conversational tone and the original stage shape (Stage 1"
        " diagnosis, Stage 2 single try-this, Stage 3 follow-up question) if present.\n"
        "- If the snippets contain no on-topic guidance for the user's query, return an empty"
        " answer string and canAnswer=false. Do not invent guidance and do not pivot to an"
        " adjacent topic.\n\n"
        "Return strict JSON only:\n"
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }'
    )
    user_prompt = (
        f"Consolidated user query:\n{query}\n\n"
        f"Numbered snippets:\n{snippets_block}\n\n"
        f"Drifting answer to repair:\n{text}\n\n"
        f"Phrases flagged as off-topic by the verifier:\n{flagged}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.1,
            max_tokens=600,
        )
    except Exception:
        return None

    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        return None

    can_answer = parsed.get("canAnswer")
    if can_answer is False:
        return None
    rewritten = parsed.get("answer")
    if not isinstance(rewritten, str):
        return None
    rewritten = rewritten.strip()
    if not rewritten:
        return None
    return rewritten


async def rewrite_to_ground(
    *,
    answer: str,
    citations: list[dict[str, Any]],
    unsupported: list[str],
) -> str | None:
    """Ask the LLM to rewrite the answer to remove unsupported specifics while
    keeping the same overall recommendation. Returns the rewritten answer text,
    or ``None`` if the rewrite cannot be produced.
    """
    text = (answer or "").strip()
    if not text or not citations:
        return None
    snippets_block = _citations_block(citations)
    flagged = "\n".join(f"- {item}" for item in unsupported[:5]) or "- (verifier did not list specific phrases)"

    system_prompt = (
        "You repair a support assistant answer that was rejected for naming UI labels or steps not "
        "present in the cited snippets. Rewrite the answer so every claim is supported by the snippets:\n"
        "- Remove or replace any specific UI label, menu path, button name, command, URL, or error "
        "code that is not in the snippets.\n"
        "- Keep the same overall recommendation in plain language. If a snippet describes the action "
        "narratively (e.g. 'switched audio device fixed it'), recommend that action plainly without "
        "inventing UI controls.\n"
        "- Preserve the warm, brief, conversational tone. Keep the original stage shape (Stage 1 "
        "diagnosis, Stage 2 single try-this, Stage 3 follow-up question) if present.\n"
        "- Cite snippets inline as [n] for each concrete recommendation.\n"
        "- If the snippets genuinely contain no usable guidance for the user's situation, return an "
        "empty answer string and canAnswer=false. Do not invent guidance.\n\n"
        "Return strict JSON only:\n"
        '{ "answer": string, "canAnswer": boolean, "usedCitations": number[] }'
    )
    user_prompt = (
        f"Numbered snippets:\n{snippets_block}\n\n"
        f"Rejected answer:\n{text}\n\n"
        f"Phrases flagged as unsupported by the verifier:\n{flagged}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.1,
            max_tokens=600,
        )
    except Exception:
        return None

    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        return None

    can_answer = parsed.get("canAnswer")
    if can_answer is False:
        return None
    rewritten = parsed.get("answer")
    if not isinstance(rewritten, str):
        return None
    rewritten = rewritten.strip()
    if not rewritten:
        return None
    return rewritten