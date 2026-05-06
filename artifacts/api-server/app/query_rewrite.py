from __future__ import annotations

import logging
import os
import re

from app import chat_agent, llm

log = logging.getLogger("api-server.query-rewrite")


_ANCHOR_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "we",
    "with",
    "you",
    "your",
}


def _normalize_string_items(raw: object, *, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = " ".join(item.split()).strip()
        if not value or value in out:
            continue
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _clip_line(text: str, *, limit: int = 240) -> str:
    compact = " ".join((text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def _normalize_query_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def _has_prior_query_context(
    recent_messages: list[dict[str, object]],
    agent_state: dict[str, object] | None,
    current: str,
) -> bool:
    normalized_state = chat_agent.normalize_state(agent_state or {})
    if str(normalized_state.get("summary") or "").strip():
        return True
    if _normalize_string_items(normalized_state.get("knownFacts"), limit=6):
        return True

    current_norm = _normalize_query_text(current)
    for message in recent_messages:
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if _normalize_query_text(content) and _normalize_query_text(content) != current_norm:
            return True

    # Assistant turns can still carry critical context if older user turns fell
    # out of the short recent-message window.
    for message in recent_messages:
        if str(message.get("role") or "") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if _normalize_query_text(content):
            return True
    return False


def _collect_context_anchors(
    recent_messages: list[dict[str, object]],
    agent_state: dict[str, object] | None,
    current: str,
) -> set[str]:
    normalized_state = chat_agent.normalize_state(agent_state or {})
    current_norm = _normalize_query_text(current)
    anchors: set[str] = set()

    def add_tokens(text: str) -> None:
        for token in _normalize_query_text(text).split():
            if len(token) < 4:
                continue
            if token in _ANCHOR_STOPWORDS:
                continue
            anchors.add(token)

    add_tokens(str(normalized_state.get("summary") or ""))
    for fact in _normalize_string_items(normalized_state.get("knownFacts"), limit=10):
        add_tokens(fact)

    for message in recent_messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if str(message.get("role") or "") == "user":
            content_norm = _normalize_query_text(content)
            if content_norm and content_norm != current_norm:
                add_tokens(content)

    return anchors


def _rewrite_is_degenerate(
    rewritten: str,
    current: str,
    recent_messages: list[dict[str, object]],
    agent_state: dict[str, object] | None,
) -> bool:
    rewritten_clean = " ".join((rewritten or "").split()).strip()
    if not rewritten_clean:
        return True
    if not _has_prior_query_context(recent_messages, agent_state, current):
        return False

    rewritten_norm = _normalize_query_text(rewritten_clean)
    current_norm = _normalize_query_text(current)
    if not rewritten_norm:
        return True
    if rewritten_norm == current_norm:
        return True

    rewritten_tokens = rewritten_norm.split()
    current_tokens = current_norm.split()
    if rewritten_tokens and current_tokens:
        rewritten_set = set(rewritten_tokens)
        current_set = set(current_tokens)
        if rewritten_set <= current_set and len(rewritten_set) <= 3:
            return True
        if current_set <= rewritten_set and len(current_set) <= 2 and len(rewritten_tokens) <= 4:
            return True

    if len(rewritten_tokens) <= 4:
        anchors = _collect_context_anchors(recent_messages, agent_state, current)
        if anchors and not any(token in anchors for token in rewritten_tokens):
            return True

    return False


def _prior_user_turns_text(
    recent_messages: list[dict[str, object]],
    current: str,
    *,
    max_turns: int = 3,
) -> str:
    """Distinct user utterances before this turn (excludes the latest short reply)."""
    cur_norm = _normalize_query_text(current)
    snippets: list[str] = []
    seen: set[str] = set()
    for message in recent_messages:
        if str(message.get("role") or "") != "user":
            continue
        raw = str(message.get("content") or "").strip()
        if not raw:
            continue
        norm = _normalize_query_text(raw)
        if not norm or norm == cur_norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        snippets.append(_clip_line(raw, limit=140))
    if not snippets:
        return ""
    return " ".join(snippets[-max_turns:]).strip()


def _build_rewrite_fallback(
    current: str,
    recent_messages: list[dict[str, object]],
    agent_state: dict[str, object] | None,
) -> str:
    normalized_state = chat_agent.normalize_state(agent_state or {})
    summary = _clip_line(str(normalized_state.get("summary") or "").strip(), limit=180).strip()
    summary_norm = _normalize_query_text(summary)

    qualifiers: list[str] = []
    seen: set[str] = set()

    def add_qualifier(value: str) -> None:
        cleaned = _clip_line(value, limit=80).strip(" .,:;-")
        normalized = _normalize_query_text(cleaned)
        if not cleaned or not normalized or normalized in seen:
            return
        if summary_norm and normalized in summary_norm:
            return
        for existing in seen:
            if len(normalized.split()) <= 2 and normalized in existing:
                return
        seen.add(normalized)
        qualifiers.append(cleaned)

    for fact in _normalize_string_items(normalized_state.get("knownFacts"), limit=6):
        add_qualifier(fact)

    add_qualifier(current)

    if not summary:
        recent_user_turns = [
            _clip_line(str(message.get("content") or "").strip(), limit=120)
            for message in recent_messages
            if str(message.get("role") or "") == "user" and str(message.get("content") or "").strip()
        ]
        deduped_turns: list[str] = []
        seen_turns: set[str] = set()
        for turn in recent_user_turns:
            normalized_turn = _normalize_query_text(turn)
            if not normalized_turn or normalized_turn in seen_turns:
                continue
            seen_turns.add(normalized_turn)
            deduped_turns.append(turn)
        if deduped_turns:
            combined = deduped_turns[-3:]
        else:
            assistant_context = [
                _clip_line(str(message.get("content") or "").strip(), limit=120)
                for message in recent_messages
                if str(message.get("role") or "") == "assistant" and str(message.get("content") or "").strip()
            ]
            combined = (
                assistant_context[-1:] + [_clip_line(current, limit=120)]
                if assistant_context
                else [_clip_line(current, limit=120)]
            )
        return _clip_line(" ".join(part for part in combined if part).strip(), limit=260)

    prior_user = _prior_user_turns_text(recent_messages, current)
    summary_clean = summary.rstrip(".")
    merged_head = summary_clean
    if prior_user:
        prior_norm = _normalize_query_text(prior_user)
        sum_norm = _normalize_query_text(summary)
        if sum_norm and sum_norm not in prior_norm:
            merged_head = f"{prior_user.rstrip('.')}. {summary_clean}"

    if qualifiers:
        return _clip_line(f"{merged_head} : {', '.join(qualifiers)}", limit=260)
    return _clip_line(merged_head, limit=260)


async def enhance_query(
    current: str,
    *,
    recent_messages: list[dict[str, object]] | None = None,
    agent_state: dict[str, object] | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Resolve follow-up references and produce a retrieval-friendly rewrite.

    Returns ``(rewritten, intent, keywords, subqueries)``. Keywords and
    subqueries are used to widen the retrieval pool with multi-query hybrid
    search so the agent gets richer grounded context.
    """
    log.info(
        "query rewrite invoked; current_len=%d recent_turns=%d",
        len((current or "").strip()),
        len(recent_messages or []),
    )

    recent = recent_messages or []
    history_block = "\n".join(
        f"{m['role']}: {(m.get('content') or '').strip()[:300]}"
        for m in recent
        if m.get("content")
    ) or "(no prior turns)"
    investigation_block = chat_agent.state_context_block(agent_state or {})

    
    sys_prompt = (
    "You enhance customer support queries for a knowledge-base retrieval step. "
    "Given the recent conversation, the investigation memory, and the user's latest message,"
    " produce a search plan that maximises grounded recall over the knowledge base.\n\n"

    "ABSOLUTE RULE — NEVER return the latest user message verbatim or anything close to it"
    " when there is ANY prior conversation context. Short replies like 'yes', 'no', 'all',"
    " 'one', 'web', 'web browser', 'desktop', 'access denied', 'mac', a number, a single"
    " word, or a short phrase are ALWAYS partial answers to a clarifying question. They are"
    " NEVER a complete query. If you output such a value as the rewrite, you have failed.\n\n"

    "CRITICAL — the rewritten query MUST be a self-contained, consolidated question that"
    " carries forward every key term established earlier in this conversation. The latest user"
    " message is almost never enough on its own: it is usually a short reply to a clarifying"
    " question (e.g. 'yes', 'all', 'web browser', 'access denied', a number, a category). Treat"
    " those replies as new facts to MERGE with the original topic, not as a new query.\n\n"

    "PRIORITY ORDER (strict):\n"
    "1. Latest user message (if it adds or corrects facts)\n"
    "2. Investigation memory - Known facts\n"
    "3. Investigation memory - Summary\n"
    "4. Earlier conversation turns\n"
    "Never override newer confirmed facts with older ones.\n\n"

    "ZERO FABRICATION RULE:\n"
    "Do NOT introduce any product, environment, error, scope, or technical detail that was not"
    " explicitly stated or logically implied in the conversation or investigation memory."
    " If a detail is unknown, omit it — do not guess.\n\n"

    "MANDATORY — anchor the rewrite to the investigation memory:\n"
    "- The 'Summary' line of the investigation memory describes the original problem the user"
    "  came in with. The rewritten query MUST preserve that problem statement (the product or"
    "  system, the symptom, and the goal). If the summary mentions Zoom, the rewrite must mention"
    "  Zoom; if the summary is about Jira access, the rewrite must be about Jira access. Never"
    "  drift to a new product or topic just because the latest message is a short reply.\n"
    "- Every 'Known facts' entry that is still relevant MUST be merged into the rewritten query"
    "  (product, version, environment, error code, scope, etc.). Do not drop confirmed facts.\n"
    "- If the latest user message contradicts the summary or a known fact, prefer the latest"
    "  message and note the change, but keep the rest of the established context.\n\n"

    "To build the rewritten query, walk the conversation and the investigation memory and"
    " collect: the original product / system the user asked about (e.g. Jira, Zoom, Outlook),"
    " the core symptom or task (e.g. access denied, cannot join, missing button), every error"
    " message or code mentioned, the scope/qualifier the user has confirmed (e.g. all projects"
    " vs one project, web vs desktop, free vs paid), and any environment detail (browser, OS,"
    " device). Then fuse them into ONE natural-language question.\n\n"

    "EDGE CASE — INSUFFICIENT CONTEXT:\n"
    "If there is not enough context to form a meaningful rewrite:\n"
    "- Use only the latest message\n"
    "- Expand minimally into a generic but valid query\n"
    "- Do NOT hallucinate missing details\n\n"

    "Output four fields:\n"
    "- rewritten: a single self-contained query, 8 to 30 words, that a search engine could"
    " answer cold without seeing this conversation. It MUST mention the product/system, the"
    " symptom, and every clarifying fact the user has already confirmed. Expand obvious"
    " acronyms. Strip greetings and pleasantries. Preserve proper nouns, error codes, product"
    " names, and exact UI strings verbatim. The query must read naturally like a human search"
    " question — not a keyword dump. NEVER return just the latest user message verbatim when"
    " prior turns established a topic — always merge.\n"

    "- intent: one of how_to | troubleshooting | billing | account | policy | general.\n"

    "- keywords: 4 to 8 short search terms or phrases that capture the most important concepts"
    " in the user's situation. Include product names, feature names, error codes, and the main"
    " symptom or action. Prefer meaningful phrases over single words where useful. Avoid filler"
    " terms (e.g. 'issue', 'problem' alone). Add 1 to 2 useful synonyms only if they improve"
    " retrieval. Do NOT invent terms or UI elements. No duplicates.\n"

    "- subqueries: 1 to 3 alternative phrasings of the same consolidated question, each under"
    " 25 words, that vary wording or angle (e.g. cause vs fix vs symptom). Each must remain"
    " self-contained, preserve the same facts, and must NOT introduce new assumptions or drift"
    " to a different topic.\n\n"

    "Worked example. Conversation so far: user said 'I have access issue on Jira', then"
    " confirmed 'all' projects, then 'Access denied', then 'web browser'. Latest message:"
    " 'web browser'. WRONG rewrite: 'web browser'. CORRECT rewrite: 'Jira access denied error"
    " for all projects when accessing via web browser — how to restore access'.\n\n"

    'Reply as JSON: {"rewritten": "<query>", "intent": "<intent>", '
    '"keywords": ["<term>", ...], "subqueries": ["<phrasing>", ...]}'
)
    
    user_prompt = (
        f"Investigation memory:\n{investigation_block}\n\n"
        f"Recent conversation:\n{history_block}\n\n"
        f"Latest user message:\n{current}"
    )

    try:
        raw = await llm.chat(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            temperature=0.3,
            max_tokens=1000,
        )
        obj = chat_agent._parse_json_object(raw)
        if not isinstance(obj, dict):
            raise ValueError("query rewrite JSON parse returned no object")

        rw_raw = obj.get("rewritten")
        if isinstance(rw_raw, str):
            rewritten = rw_raw.strip() or current
        elif rw_raw is None:
            rewritten = current
        else:
            rewritten = str(rw_raw).strip() or current

        intent_raw = obj.get("intent")
        if isinstance(intent_raw, str):
            intent = intent_raw.strip() or "general"
        elif intent_raw is None:
            intent = "general"
        else:
            intent = str(intent_raw).strip() or "general"

        keywords = _normalize_string_items(obj.get("keywords"), limit=10)
        subqueries = _normalize_string_items(obj.get("subqueries"), limit=3)
        if _rewrite_is_degenerate(rewritten, current, recent, agent_state):
            fallback = _build_rewrite_fallback(current, recent, agent_state)
            if fallback:
                log.info(
                    "query rewrite fallback used; llm_rewrite=%r fallback=%r",
                    rewritten,
                    fallback,
                )
                rewritten = fallback
        log.info(
            "query rewrite completed; intent=%s keywords=%d subqueries=%d",
            intent,
            len(keywords),
            len(subqueries),
        )
        return rewritten, intent, keywords, subqueries
    except Exception as err:
        log.warning("query enhancement failed: %s", err)
        if _has_prior_query_context(recent, agent_state, current):
            fallback = _build_rewrite_fallback(current, recent, agent_state)
            if fallback.strip():
                return fallback.strip(), "general", [], []
        return current, "general", [], []