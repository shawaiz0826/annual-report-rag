"""Web chat over ChatService — the browser version of the terminal chat.

Run with `make ui` (opens http://localhost:8501). Pages: Chat, Catalog, Facts, Search.
Every answer is produced live by retrieval → facts → model; the "How this was answered"
panel under each answer is the web form of the CLI's `/why`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # `streamlit run apps/...` puts apps/ first

from core.models import AgentAnswer, Trace  # noqa: E402
from core.observability.tracing import render_trace  # noqa: E402
from core.service import ChatService  # noqa: E402

st.set_page_config(
    page_title="Annual-report analyst",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

SUGGESTED_QUESTIONS = [
    "What was Tesla's revenue in 2023?",
    "What was BMW's total revenue in 2023?",
    "Between Tesla and Ford, which company achieved higher profits in 2022?",
    "Can you provide the revenue figures for BMW in 2017?",
    "What were the growth trends for BMW's financial performance from 2020 to 2023?",
    "What key economic factors influenced Ford's performance in 2021?",
    "Which Tesla product is currently in the development stage?",
    "What was Toyota's revenue in 2023?",
]

CONFIDENCE_COLOUR = {"high": "green", "medium": "orange", "low": "red"}


@st.cache_resource(show_spinner="Loading catalog, indexes and facts…")
def get_service() -> ChatService:
    """One service per process; the embedding model loads on the first search."""
    return ChatService()


service = get_service()
settings = service.settings


# ----------------------------------------------------------------------------- state


def _init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = service.new_session().session_id
        st.session_state.history = []
    st.session_state.setdefault("pending", None)


def _new_conversation() -> None:
    st.session_state.session_id = service.new_session().session_id
    st.session_state.history = []
    st.session_state.pending = None


def _queue_suggestion() -> None:
    """Pill callback: send the chosen question as if it had been typed."""
    chosen = st.session_state.get("suggestion")
    if chosen:
        st.session_state.pending = chosen
        st.session_state.suggestion = None


_init_state()


# ----------------------------------------------------------------------------- sidebar

with st.sidebar:
    st.title("📊 Annual-report analyst")
    st.caption(
        "Answers come only from the ingested reports. Every figure is a verified, cited fact; "
        "every calculation runs in Python; every answer has a trace."
    )
    page = st.radio("Page", ["Chat", "Catalog", "Facts", "Search"], horizontal=True)

    st.divider()
    st.subheader("Model")
    known = list(
        dict.fromkeys([settings.llm_model, settings.llm_model_fallback, *settings.llm.known_models])
    )
    model = st.selectbox("Primary model", known, index=0, label_visibility="collapsed")
    custom_model = st.text_input("Other model id", placeholder="e.g. anthropic/claude-sonnet-5")
    if custom_model.strip():
        model = custom_model.strip()
    if settings.llm.model_fallback and settings.llm_model_fallback:
        st.caption(
            f"Fallback on failure: `{settings.llm_model_fallback}` — the answer says when it was used."
        )
    show_knowledge = st.toggle(
        "Add labelled model knowledge",
        value=False,
        help="Off = grounded only. On = the model may add a clearly labelled, unverified note "
        "from its own knowledge, kept separate from the grounded answer.",
    )
    st.button("New conversation", on_click=_new_conversation, width="stretch")

    st.divider()
    stats = service.index_stats()
    st.caption(
        f"**Index** · {stats['documents']} documents · {stats['chunks']:,} chunks · "
        f"{stats['facts']:,} facts"
    )
    coverage = service.catalog.coverage()
    if coverage:
        st.caption("**Coverage** (legal entity → fiscal years held)")
        for entity, years in coverage.items():
            st.caption(f"{entity}: {', '.join(map(str, years))}")
    st.caption(f"session `{st.session_state.session_id}`")


# ----------------------------------------------------------------------------- rendering


def _df(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{c: r.get(c) for c in columns} for r in rows], columns=columns)


def render_answer(answer: AgentAnswer, trace: Trace) -> None:
    """Answer body, sources, caveats, footer and the explanation panel."""
    st.markdown(answer.answer_markdown)
    if answer.model_knowledge_note:
        st.info(answer.model_knowledge_note, icon="💭")
    for caveat in answer.caveats:
        st.warning(caveat, icon="⚠️")
    if answer.citations:
        seen: dict[str, str] = {}
        for c in answer.citations:
            seen.setdefault(c.label(), c.file_name)
        st.caption(
            "**Sources** · " + " · ".join(f"{label} — `{file}`" for label, file in seen.items())
        )

    u = trace.usage
    cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "n/a"
    colour = CONFIDENCE_COLOUR.get(answer.confidence, "gray")
    st.caption(
        f":{colour}-badge[confidence {answer.confidence}] · {trace.model} · "
        f"{u.prompt_tokens}/{u.completion_tokens} tokens · {cost} · "
        f"{trace.latency_ms / 1000:.1f}s · {len(trace.tool_calls)} tool call(s) · "
        f"trace `{trace.trace_id}`"
    )

    with st.expander("How this was answered"):
        tabs = st.tabs(["Tool calls", "Retrieved chunks", "Facts used", "Trace"])
        with tabs[0]:
            if trace.fallback_from:
                st.warning(
                    f"Answered by **{trace.model}** after **{trace.fallback_from}** failed: "
                    f"{trace.fallback_reason}",
                    icon="🔁",
                )
            if not trace.tool_calls:
                st.caption(
                    "No tool calls were made." + (f" Error: {trace.error}" if trace.error else "")
                )
            for call in trace.tool_calls:
                st.markdown(
                    f"**{call.step}. `{call.tool}`** — {call.result_summary} "
                    f"<span style='color:gray'>({call.latency_ms} ms)</span>",
                    unsafe_allow_html=True,
                )
                st.code(json.dumps(call.arguments, ensure_ascii=False), language="json")
                if call.result_ref:
                    try:
                        st.json(service.traces.read_result(call.result_ref), expanded=False)
                    except OSError:
                        st.caption("full result not available")
        with tabs[1]:
            if trace.retrieved:
                st.dataframe(
                    _df(
                        trace.retrieved,
                        [
                            "legal_entity",
                            "fiscal_year",
                            "page",
                            "dense_rank",
                            "lexical_rank",
                            "rrf_score",
                            "expanded",
                            "chunk_id",
                        ],
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    "dense = embedding rank, lexical = BM25 rank, rrf = fused score; "
                    "expanded = the whole page was shown to the model."
                )
            else:
                st.caption("No passages were retrieved for this answer (facts only).")
        with tabs[2]:
            if trace.facts_used:
                st.dataframe(
                    _df(
                        trace.facts_used,
                        [
                            "legal_entity",
                            "metric",
                            "fiscal_year",
                            "formatted",
                            "page",
                            "source_fiscal_year",
                            "is_comparative",
                            "confidence",
                            "reconciliation_note",
                            "fact_id",
                        ],
                    ),
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("No facts from the store were used.")
        with tabs[3]:
            st.code(render_trace(trace), language="text")


# ----------------------------------------------------------------------------- pages


def page_chat() -> None:
    st.pills(
        "Try one",
        SUGGESTED_QUESTIONS,
        selection_mode="single",
        key="suggestion",
        on_change=_queue_suggestion,
        label_visibility="collapsed",
    )

    for item in st.session_state.history:
        with st.chat_message(item["role"]):
            if item["role"] == "assistant":
                render_answer(item["answer"], item["trace"])
            else:
                st.markdown(item["content"])

    prompt = st.chat_input("Ask about the annual reports… e.g. What was Tesla's revenue in 2023?")
    if not prompt and st.session_state.pending:
        prompt = st.session_state.pending
        st.session_state.pending = None
    if not prompt:
        return

    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Retrieving, looking up facts, asking the model…"):
                answer, trace = service.chat(
                    st.session_state.session_id,
                    prompt,
                    model=model,
                    show_model_knowledge=show_knowledge,
                )
        except Exception as exc:  # keep the page alive; the error is the answer
            st.error(f"{type(exc).__name__}: {exc}")
            st.session_state.history.append({"role": "user", "content": prompt})
            return
    st.session_state.history.append({"role": "user", "content": prompt})
    st.session_state.history.append(
        {"role": "assistant", "content": answer.answer_markdown, "answer": answer, "trace": trace}
    )
    st.rerun()


def page_catalog() -> None:
    st.subheader("Document catalog")
    st.caption(
        "Discovered at ingest by the profiler and validated in code — nothing here was configured "
        "by hand. Subsidiary reports, missing years and noise documents are what the agent reasons about."
    )
    rows = []
    for d in service.catalog.documents():
        s = service.catalog.stats(d.document_id)
        rows.append(
            {
                "issuer": d.issuer,
                "legal entity": d.legal_entity,
                "type": d.doc_type,
                "entity": d.entity_type,
                "primary issuer report": d.is_primary_issuer_report,
                "FY": d.fiscal_year,
                "currency": d.reporting_currency,
                "unit": d.reporting_unit,
                "pages": d.page_count,
                "chunks": s.get("chunks"),
                "facts": s.get("facts"),
                "file": d.file_name,
            }
        )
    if not rows:
        st.warning("The catalog is empty — run `make rebuild` (or `make ingest`) first.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown("**Coverage** — legal entity → fiscal years of documents held")
    for entity, years in service.catalog.coverage().items():
        st.markdown(f"- {entity}: {', '.join(map(str, years))}")
    st.markdown("**Profiles**")
    for d in service.catalog.documents():
        title = f"{d.file_name} — {d.legal_entity or d.doc_type}"
        with st.expander(title):
            st.write(d.summary or "_no summary_")
            meta = {
                "document_id": d.document_id,
                "parent_organisation": d.parent_organisation,
                "sector": d.sector,
                "period_end": d.period_end,
                "profiler_confidence": d.profiler_confidence,
                "profiler_model": d.profiler_model,
                "warnings": d.warnings,
            }
            st.json({k: v for k, v in meta.items() if v not in (None, "", [])}, expanded=False)


def page_facts() -> None:
    st.subheader("Facts store")
    st.caption(
        "Every (entity, metric, fiscal year) figure the extractor found, reconciled across reports. "
        "Confidence: 0.8 original, 0.6 comparative, 0.95 when two reports agree."
    )
    issuers = ["All", *sorted(service.catalog_view()["issuers"].keys())]
    metrics = ["All", *service.registry.names()]
    c1, c2, c3 = st.columns([1, 1, 1])
    issuer = c1.selectbox("Issuer", issuers)
    metric = c2.selectbox("Metric", metrics)
    show_all = c3.toggle("Include non-canonical / unusable rows", value=False)
    rows = service.fact_store.lookup(
        issuer=None if issuer == "All" else service.catalog.resolve_issuer(issuer),
        metric=None if metric == "All" else metric,
        canonical_only=not show_all,
        usable_only=not show_all,
    )
    if not rows:
        st.info(
            "No facts match. Coverage: " + json.dumps(service.fact_store.coverage(), default=list)
        )
        return
    years = sorted({r.fiscal_year for r in rows})
    chosen = st.multiselect("Fiscal years", years, default=years)
    table = [
        {
            "legal entity": r.legal_entity,
            "metric": r.metric,
            "FY": r.fiscal_year,
            "value": r.formatted(),
            "page": r.page,
            "source FY": r.source_fiscal_year,
            "comparative": r.is_comparative,
            "confidence": r.confidence,
            "note": r.reconciliation_note or "",
            "label seen": r.metric_alias_seen,
            "file": r.file_name,
            "fact id": r.fact_id,
        }
        for r in rows
        if r.fiscal_year in chosen
    ]
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    st.caption(f"{len(table)} facts")


def page_search() -> None:
    st.subheader("Hybrid search")
    st.caption(
        "What the `search_documents` tool sees: dense (BGE-small) and BM25 candidates fused with "
        "reciprocal rank fusion, round-robin across issuers, numeric pages expanded. "
        "The fiscal year is never a hard filter — prior-year figures live inside later reports."
    )
    query = st.text_input("Query", placeholder="BMW group revenues 2017")
    c1, c2 = st.columns([2, 1])
    issuers = c1.multiselect(
        "Restrict to issuers", sorted(service.catalog_view()["issuers"].keys())
    )
    k = c2.slider("Results", 3, 20, settings.retrieval.top_k)
    if not query.strip():
        return
    with st.spinner("Searching…"):
        hits = service.search(query, issuers=issuers or None, k=k)
    if not hits:
        st.info("No results.")
        return
    table = [
        {
            "#": i,
            "legal entity": h.chunk.metadata.get("legal_entity"),
            "FY": h.chunk.metadata.get("fiscal_year"),
            "type": h.chunk.metadata.get("doc_type"),
            "page": h.chunk.page,
            "dense": h.dense_rank,
            "lexical": h.lexical_rank,
            "rrf": round(h.rrf_score, 4),
            "expanded": h.expanded_text is not None,
            "file": h.chunk.metadata.get("file_name"),
        }
        for i, h in enumerate(hits, start=1)
    ]
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    for i, h in enumerate(hits, start=1):
        label = f"{i}. {h.chunk.metadata.get('legal_entity') or h.chunk.metadata.get('doc_type')} p.{h.chunk.page} — rrf {h.rrf_score:.4f}"
        with st.expander(label):
            st.text(h.best_text)


if page == "Chat":
    page_chat()
elif page == "Catalog":
    page_catalog()
elif page == "Facts":
    page_facts()
else:
    page_search()
