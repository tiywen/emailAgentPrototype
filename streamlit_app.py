from __future__ import annotations

import os

from email_assistant.dotenv_load import load_project_dotenv

load_project_dotenv(override=True)

import streamlit as st

from email_assistant.graph_mail import (
    get_message_detail,
    graph_message_to_thread_text,
    graph_base_url,
    graph_get_me,
    graph_probe_me,
    list_inbox_messages,
)
from email_assistant.jwt_peek import (
    normalize_jwt_access_token,
    peek_access_token_claims,
    summarize_claims_for_ui,
)
from email_assistant.msal_device import (
    GRAPH_SCOPES,
    build_public_client,
    complete_device_flow,
    describe_authority,
    initiate_device_flow,
    try_acquire_token_silent,
)
from msal import SerializableTokenCache
from email_assistant.summary_pipeline import analysis_to_dict, analyze_thread_text

# --- Page ---
st.set_page_config(page_title="Email Assistant (Graph)", layout="wide")

SESSION_TOKEN = "graph_access_token"
SESSION_EMAILS = "inbox_messages_cache"
SESSION_MSAL_DIAG = "msal_authority_diag"
SESSION_TOKEN_CLAIMS = "graph_token_claims_summary"
SESSION_MSAL_CACHE = "msal_serializable_token_cache"
SESSION_SELECTED_MESSAGE_ID = "selected_message_id"
SESSION_ANALYSIS_CACHE = "analysis_cache"
SESSION_PROCESSING = "ui_processing"
SESSION_SIGNED_IN_USER = "signed_in_user_profile"


def _logout() -> None:
    st.session_state.pop(SESSION_TOKEN, None)
    st.session_state.pop(SESSION_MSAL_CACHE, None)
    st.session_state.pop(SESSION_EMAILS, None)
    st.session_state.pop(SESSION_MSAL_DIAG, None)
    st.session_state.pop(SESSION_TOKEN_CLAIMS, None)
    st.session_state.pop(SESSION_SELECTED_MESSAGE_ID, None)
    st.session_state.pop(SESSION_ANALYSIS_CACHE, None)
    st.session_state.pop(SESSION_PROCESSING, None)
    st.session_state.pop(SESSION_SIGNED_IN_USER, None)


def _resolve_graph_access_token() -> str | None:
    """Prefer MSAL silent refresh from serialized cache; fallback to normalized JWT in session."""
    ser = st.session_state.get(SESSION_MSAL_CACHE)
    if ser:
        try:
            tok, new_ser = try_acquire_token_silent(ser)
            st.session_state[SESSION_MSAL_CACHE] = new_ser
            if tok:
                return tok
        except (ValueError, TypeError, OSError):
            st.session_state.pop(SESSION_MSAL_CACHE, None)
    return normalize_jwt_access_token(st.session_state.get(SESSION_TOKEN))


def _session_has_login_keys() -> bool:
    return bool(st.session_state.get(SESSION_MSAL_CACHE) or st.session_state.get(SESSION_TOKEN))

def _is_processing() -> bool:
    return bool(st.session_state.get(SESSION_PROCESSING))


def _set_processing(value: bool) -> None:
    st.session_state[SESSION_PROCESSING] = bool(value)


def _cache_get(message_id: str, style: str) -> dict | None:
    cache = st.session_state.get(SESSION_ANALYSIS_CACHE) or {}
    return cache.get(f"{message_id}:{style}")


def _cache_set(message_id: str, style: str, payload: dict) -> None:
    cache = st.session_state.get(SESSION_ANALYSIS_CACHE) or {}
    cache[f"{message_id}:{style}"] = payload
    st.session_state[SESSION_ANALYSIS_CACHE] = cache


def main() -> None:
    st.title("Email Assistant — Microsoft Graph")
    st.caption("Device code login (public client) · Delegated User.Read + Mail.ReadWrite")

    with st.sidebar:
        st.subheader("Entra ID")
        client_hint = os.getenv("AZURE_CLIENT_ID") or os.getenv("MICROSOFT_CLIENT_ID")
        tenant_hint = os.getenv("AZURE_TENANT_ID") or os.getenv("MICROSOFT_TENANT_ID")
        st.text_input("Client ID (env)", value=client_hint or "(not set)", disabled=True)
        st.text_input("Tenant ID (env)", value=tenant_hint or "(not set)", disabled=True)
        st.markdown(f"Scopes: `{', '.join(GRAPH_SCOPES)}`")
        st.caption("提示：本应用已请求 `Mail.ReadWrite`（为后续写入草稿箱/草稿邮件做准备）。")
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

        user_profile = st.session_state.get(SESSION_SIGNED_IN_USER) or {}
        if user_profile:
            email = (user_profile.get("mail") or user_profile.get("userPrincipalName") or "").strip()
            name = (user_profile.get("displayName") or "").strip()
            if email or name:
                st.markdown("**Signed in as**")
                st.write(name or "(no display name)")
                st.write(email or "(no email)")

        if _session_has_login_keys():
            if st.button("Sign out", type="secondary"):
                _logout()
                st.rerun()
        else:
            if st.button("Sign in (device code)", type="primary", disabled=_is_processing()):
                try:
                    _set_processing(True)
                    cache = SerializableTokenCache()
                    app = build_public_client(token_cache=cache)
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
                        st.session_state[SESSION_MSAL_CACHE] = cache.serialize()
                        claims = peek_access_token_claims(raw_token)
                        st.session_state[SESSION_TOKEN_CLAIMS] = summarize_claims_for_ui(claims)
                        # Fetch signed-in user identity once for display
                        try:
                            st.session_state[SESSION_SIGNED_IN_USER] = graph_get_me(raw_token)
                        except Exception:
                            st.session_state.pop(SESSION_SIGNED_IN_USER, None)
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
                finally:
                    _set_processing(False)

    token = _resolve_graph_access_token()
    if not token:
        if _session_has_login_keys():
            st.error(
                "无法从会话中解析有效访问令牌（例如 JWT 损坏或缓存失效）。"
                "请点击侧栏 **Sign out** 后重新登录。"
            )
        else:
            st.warning("Sign in from the sidebar to load your inbox.")
        return

    st.session_state[SESSION_TOKEN_CLAIMS] = summarize_claims_for_ui(peek_access_token_claims(token))
    if SESSION_SIGNED_IN_USER not in st.session_state:
        try:
            st.session_state[SESSION_SIGNED_IN_USER] = graph_get_me(token)
        except Exception:
            pass

    # OpenAI for analysis
    if not os.getenv("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY is not set. Add it to `.env` for summarization.")
        return

    model = st.text_input("OpenAI model", value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    st.caption(
        f"Graph API：`{graph_base_url()}` · 列表优先收件箱；"
        f"若 401 再核对 `GRAPH_API_ROOT`"
    )

    # Two-row layout so the button aligns with the number field (not with the input's built-in label).
    hdr_l, hdr_r = st.columns([1, 3])
    with hdr_l:
        st.caption("收件箱")
    with hdr_r:
        st.caption("Messages to fetch")
    col_load, col_top = st.columns([1, 3])
    with col_load:
        load_clicked = st.button(
            "Refresh inbox",
            type="primary",
            use_container_width=True,
            disabled=_is_processing(),
        )
    with col_top:
        top_n = st.number_input(
            "messages_to_fetch",
            min_value=1,
            max_value=50,
            value=15,
            step=1,
            label_visibility="collapsed",
            disabled=_is_processing(),
        )

    if load_clicked or SESSION_EMAILS not in st.session_state:
        try:
            _set_processing(True)
            with st.spinner("Loading inbox…"):
                messages = list_inbox_messages(token, top=int(top_n))
            st.session_state[SESSION_EMAILS] = messages
        except Exception as e:
            st.error(str(e))
            err_text = str(e)
            if "401" in err_text or "Unauthorized" in err_text:
                if "InvalidAuthenticationToken" in err_text or "no dots" in err_text.lower():
                    st.warning(
                        "Graph 认为 Authorization 里的 **JWT 格式无效**（常见：会话里令牌被截断/污染）。"
                        "已改为用 MSAL 缓存静默刷新；请 **Sign out** 后重新登录一次以写入新缓存。"
                    )
                probe_status, probe_body = graph_probe_me(token)
                st.warning(
                    "Graph 返回 **401**：也可能是 **Graph 主机与租户云不一致**，"
                    "或访问令牌 `aud` 不是 Microsoft Graph。"
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
        finally:
            _set_processing(False)

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

    choice = st.selectbox("Select a message", options=labels, index=0, disabled=_is_processing())
    message_id = options[choice]

    prev_selected = st.session_state.get(SESSION_SELECTED_MESSAGE_ID)
    selection_changed = (prev_selected is not None) and (prev_selected != message_id)
    st.session_state[SESSION_SELECTED_MESSAGE_ID] = message_id

    st.subheader("Summarize")
    b1, b2 = st.columns([1, 1])
    with b1:
        do_short = st.button(
            "Short version summary",
            type="primary",
            use_container_width=True,
            disabled=_is_processing(),
        )
    with b2:
        do_long = st.button(
            "Long version summary",
            type="secondary",
            use_container_width=True,
            disabled=_is_processing(),
        )

    # Auto-run short summary when selection changes (after first render)
    should_run = do_long or do_short or selection_changed
    style = "long" if do_long else "short"

    if should_run:
        # If auto-triggered by selection change, default to short
        if selection_changed and not (do_long or do_short):
            style = "short"
        try:
            _set_processing(True)
            cached = _cache_get(message_id, style)
            if cached:
                out = cached.get("out") or {}
                thread_text = cached.get("thread_text") or ""
            else:
                with st.spinner("Fetching full message and calling model…"):
                    detail = get_message_detail(token, message_id)
                    thread_text = graph_message_to_thread_text(detail)
                    result = analyze_thread_text(thread_text, model=model, style=style)
                out = analysis_to_dict(result)
                _cache_set(message_id, style, {"out": out, "thread_text": thread_text})

            # Natural language output
            st.subheader("Answer")
            summary_text = (out.get("summary") or "").strip()
            points = out.get("key_points") or []
            questions = out.get("open_questions") or []

            if summary_text:
                st.markdown(summary_text)
            else:
                st.info("No summary returned.")

            if points:
                st.markdown("**Key points**")
                st.markdown("\n".join([f"- {p}" for p in points]))

            if questions:
                st.markdown("**Open questions**")
                st.markdown("\n".join([f"- {q}" for q in questions]))

            # Debug output at the bottom (collapsible)
            st.divider()
            with st.expander("Structured JSON (debug)"):
                st.json(out)
            with st.expander("Preprocessed text sent to model (debug)"):
                st.code(thread_text, language="text")

        except Exception as e:
            st.error(str(e))
            if "401" in str(e):
                probe_status, probe_body = graph_probe_me(token)
                st.markdown(f"**探测** `GET .../me` → HTTP `{probe_status}`")
                st.code(probe_body or "(empty body)", language="text")
        finally:
            _set_processing(False)


if __name__ == "__main__":
    main()
