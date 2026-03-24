from __future__ import annotations

import os

from email_assistant.dotenv_load import load_project_dotenv

load_project_dotenv(override=True)

import streamlit as st

from email_assistant.graph_mail import (
    get_message_detail,
    graph_message_to_thread_text,
    graph_base_url,
    graph_probe_me,
    list_inbox_messages,
)
from email_assistant.jwt_peek import peek_access_token_claims, summarize_claims_for_ui
from email_assistant.msal_device import (
    GRAPH_SCOPES,
    build_public_client,
    complete_device_flow,
    describe_authority,
    initiate_device_flow,
)
from email_assistant.summary_pipeline import analysis_to_dict, analyze_thread_text

# --- Page ---
st.set_page_config(page_title="Email Assistant (Graph)", layout="wide")

SESSION_TOKEN = "graph_access_token"
SESSION_EMAILS = "inbox_messages_cache"
SESSION_MSAL_DIAG = "msal_authority_diag"
SESSION_TOKEN_CLAIMS = "graph_token_claims_summary"


def _logout() -> None:
    st.session_state.pop(SESSION_TOKEN, None)
    st.session_state.pop(SESSION_EMAILS, None)
    st.session_state.pop(SESSION_MSAL_DIAG, None)
    st.session_state.pop(SESSION_TOKEN_CLAIMS, None)


def main() -> None:
    st.title("Email Assistant — Microsoft Graph")
    st.caption("Device code login (public client) · Delegated User.Read + Mail.Read")

    with st.sidebar:
        st.subheader("Entra ID")
        client_hint = os.getenv("AZURE_CLIENT_ID") or os.getenv("MICROSOFT_CLIENT_ID")
        tenant_hint = os.getenv("AZURE_TENANT_ID") or os.getenv("MICROSOFT_TENANT_ID")
        st.text_input("Client ID (env)", value=client_hint or "(not set)", disabled=True)
        st.text_input("Tenant ID (env)", value=tenant_hint or "(not set)", disabled=True)
        st.markdown(f"Scopes: `{', '.join(GRAPH_SCOPES)}`")
        st.caption(
            "若出现 AADSTS700016 且 directory 为空：在 `.env` 试加 "
            "`AZURE_MSAL_DISABLE_INSTANCE_DISCOVERY=true`；或核对「应用注册」与「目录租户 ID」是否同一租户。"
        )
        if os.getenv("AZURE_AUTHORITY"):
            st.caption(f"当前 authority：`{os.getenv('AZURE_AUTHORITY')}`")
        else:
            st.caption(
                "**个人 Outlook（MSA）读邮件若 `/me` 成功但列表 401**：在 `.env` 添加 "
                "`AZURE_AUTHORITY=https://login.microsoftonline.com/common`，应用注册须选 "
                "「任何组织目录 + 个人 Microsoft 帐户」，**Sign out 后重新登录**。"
            )
        if st.session_state.get(SESSION_MSAL_DIAG):
            with st.expander("MSAL 元数据（排查登录）"):
                st.json(st.session_state[SESSION_MSAL_DIAG])
        if st.session_state.get(SESSION_TOKEN_CLAIMS):
            with st.expander("访问令牌摘要（aud / scp，不含密钥）"):
                st.json(st.session_state[SESSION_TOKEN_CLAIMS])

        if st.session_state.get(SESSION_TOKEN):
            if st.button("Sign out", type="secondary"):
                _logout()
                st.rerun()
        else:
            if st.button("Sign in (device code)", type="primary"):
                try:
                    app = build_public_client()
                    st.session_state[SESSION_MSAL_DIAG] = describe_authority(app)
                    flow = initiate_device_flow(app)
                    st.info(flow["message"])
                    with st.spinner("Complete sign-in in the browser. Waiting for token…"):
                        result = complete_device_flow(app, flow)
                    if "access_token" not in result:
                        err = result.get("error_description") or result.get("error") or str(result)
                        st.error(f"Login failed: {err}")
                    else:
                        raw_token = (result.get("access_token") or "").strip()
                        st.session_state[SESSION_TOKEN] = raw_token
                        claims = peek_access_token_claims(raw_token)
                        st.session_state[SESSION_TOKEN_CLAIMS] = summarize_claims_for_ui(claims)
                        st.session_state.pop(SESSION_EMAILS, None)
                        st.success("Signed in.")
                        st.rerun()
                except Exception as e:
                    if SESSION_MSAL_DIAG not in st.session_state:
                        try:
                            app = build_public_client()
                            st.session_state[SESSION_MSAL_DIAG] = describe_authority(app)
                        except Exception:
                            pass
                    st.exception(e)

    token = st.session_state.get(SESSION_TOKEN)
    if not token:
        st.warning("Sign in from the sidebar to load your inbox.")
        return

    if not st.session_state.get(SESSION_TOKEN_CLAIMS):
        st.session_state[SESSION_TOKEN_CLAIMS] = summarize_claims_for_ui(peek_access_token_claims(token))

    # OpenAI for analysis
    if not os.getenv("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY is not set. Add it to `.env` for summarization.")
        return

    model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    st.caption(
        f"Graph API：`{graph_base_url()}` · 列表使用 `GET /me/messages`（兼容个人 Outlook/MSA；"
        f"若 401 再核对 `GRAPH_API_ROOT`）"
    )

    col_load, col_top = st.columns([1, 3])
    with col_load:
        load_clicked = st.button("Refresh inbox", type="primary")
    with col_top:
        top_n = st.number_input("Messages to fetch", min_value=1, max_value=50, value=15, step=1)

    if load_clicked or SESSION_EMAILS not in st.session_state:
        try:
            with st.spinner("Loading inbox…"):
                messages = list_inbox_messages(token, top=int(top_n))
            st.session_state[SESSION_EMAILS] = messages
        except Exception as e:
            st.error(str(e))
            err_text = str(e)
            if "401" in err_text or "Unauthorized" in err_text:
                probe_status, probe_body = graph_probe_me(token)
                st.warning(
                    "Graph 返回 **401**：通常是 **Graph 主机与租户云不一致**（例如中国区/政府云仍访问 graph.microsoft.com），"
                    "或访问令牌 `aud` 不是 Microsoft Graph。请勿当成「会话过期」。"
                )
                st.markdown(f"**探测** `GET .../me` → HTTP `{probe_status}`")
                st.code(probe_body or "(empty body)", language="text")
                if st.session_state.get(SESSION_TOKEN_CLAIMS):
                    st.markdown("**当前令牌声明（摘要）**")
                    st.json(st.session_state[SESSION_TOKEN_CLAIMS])
                st.info(
                    "请先检查 `.env`：`GRAPH_API_ROOT`。"
                    " 国际版默认 `https://graph.microsoft.com/v1.0`；"
                    " 中国云常见为 `https://microsoftgraph.chinacloudapi.cn/v1.0`；"
                    " 美国政府云为 `https://graph.microsoft.us/v1.0`。"
                    " 修改后重启 Streamlit。仍失败请在侧栏 **Sign out** 后重新登录。"
                )
            return

    messages = st.session_state.get(SESSION_EMAILS) or []
    if not messages:
        st.info("Inbox is empty or could not be loaded.")
        return

    options: dict[str, str] = {}
    labels: list[str] = []
    for m in messages:
        mid = m.get("id") or ""
        subject = (m.get("subject") or "(no subject)").strip()
        ts = (m.get("receivedDateTime") or "")[:19].replace("T", " ")
        frm = m.get("from") or {}
        addr = (frm.get("emailAddress") or {}).get("address") or ""
        label = f"{ts} · {subject[:80]}{'…' if len(subject) > 80 else ''} · {addr}"
        labels.append(label)
        options[label] = mid

    choice = st.selectbox("Select a message", options=labels, index=0)
    message_id = options[choice]

    if st.button("Analyze full body with assistant", type="primary"):
        try:
            with st.spinner("Fetching full message and calling model…"):
                detail = get_message_detail(token, message_id)
                thread_text = graph_message_to_thread_text(detail)
                result = analyze_thread_text(thread_text, model=model)
            out = analysis_to_dict(result)
            st.subheader("Structured summary")
            st.json(out)
            with st.expander("Preprocessed text sent to model"):
                st.code(thread_text, language="text")
        except Exception as e:
            st.error(str(e))
            if "401" in str(e):
                probe_status, probe_body = graph_probe_me(token)
                st.markdown(f"**探测** `GET .../me` → HTTP `{probe_status}`")
                st.code(probe_body or "(empty body)", language="text")


if __name__ == "__main__":
    main()
