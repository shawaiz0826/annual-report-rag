"""Minimal Streamlit UI over ChatService. Run with `make ui`."""

from __future__ import annotations

import streamlit as st

from core.service import ChatService

st.set_page_config(page_title="Annual-report analyst", page_icon="📊", layout="wide")


@st.cache_resource
def get_service() -> ChatService:
    return ChatService()


service = get_service()
settings = service.settings

with st.sidebar:
    st.title("Annual-report analyst")
    models = list(
        dict.fromkeys([settings.llm_model, settings.llm_model_fallback, *settings.llm.known_models])
    )
    model = st.selectbox("Model", models, index=0)
    show_knowledge = st.toggle("Show labelled model knowledge", value=False)
    if st.button("New session"):
        st.session_state.pop("session_id", None)
        st.session_state.pop("history", None)
    st.caption(f"{service.index_stats()}")
    with st.expander("Catalog"):
        for issuer, docs in service.catalog_view()["issuers"].items():
            st.markdown(f"**{issuer}**")
            for d in docs:
                st.caption(
                    f"{d['legal_entity']} · {d['entity_type']} · FY{d['fiscal_year']} · {d['currency']} {d['unit']}"
                )

if "session_id" not in st.session_state:
    st.session_state.session_id = service.new_session().session_id
    st.session_state.history = []

for item in st.session_state.history:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        if item.get("answer") is not None:
            answer, trace = item["answer"], item["trace"]
            with st.expander("Sources, caveats and trace"):
                for c in answer.citations:
                    st.caption(f"{c.label()} — {c.file_name}")
                for cav in answer.caveats:
                    st.warning(cav)
                if answer.model_knowledge_note:
                    st.info(answer.model_knowledge_note)
                st.code(service.explain(trace.trace_id))

if prompt := st.chat_input("Ask about the annual reports…"):
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"), st.spinner("thinking…"):
        answer, trace = service.chat(
            st.session_state.session_id, prompt, model=model, show_model_knowledge=show_knowledge
        )
        st.markdown(answer.answer_markdown)
        u = trace.usage
        cost = f"${u.cost_usd:.4f}" if u.cost_usd is not None else "n/a"
        st.caption(
            f"{trace.model} · {u.prompt_tokens}/{u.completion_tokens} tokens · {cost} · {trace.latency_ms / 1000:.1f}s · confidence {answer.confidence}"
        )
        with st.expander("Sources, caveats and trace"):
            for c in answer.citations:
                st.caption(f"{c.label()} — {c.file_name}")
            for cav in answer.caveats:
                st.warning(cav)
            if answer.model_knowledge_note:
                st.info(answer.model_knowledge_note)
            st.code(service.explain(trace.trace_id))
    st.session_state.history.append(
        {"role": "assistant", "content": answer.answer_markdown, "answer": answer, "trace": trace}
    )
