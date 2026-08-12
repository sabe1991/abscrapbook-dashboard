"""AB Scrapbook ダッシュボードの公開用アプリ(Streamlit)。

役割:
  1. GitHub の非公開リポジトリ(`abscrapbook-data`)から記事一覧(latest.json)を取得して表示
  2. 保存・フォルダ移動・削除の操作を、GitHub の非公開リポジトリ(`abscrapbook-inbox`)への
     指示ファイル書き込みとして送信する(実際の反映は次にスマホでAB Scrapbookを開いたとき)

AB Workout ダッシュボード(`../../ワークアウトダッシュボード/dashboard/app.py`)と違い、こちらは
保存・移動・削除という書き込み操作が要るため、`view.html` + JS埋め込み方式ではなく、
素のStreamlitウィジェット(selectbox・button・popover等)で組んでいる
(実装計画フェーズ3の決定どおり)。書き込み用トークンは常にサーバー側(このスクリプト)だけが持ち、
ブラウザ・ブックマークレットには一切渡さない。

ローカルで動かす場合:
  cd dashboard
  streamlit run app.py
  (GitHub の設定が無いときは、自動的に ../sample-data/latest.json を表示する)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from html import escape

import streamlit as st

from github_client import RepoConfig, get_json, put_json

HERE = os.path.dirname(os.path.abspath(__file__))
# ローカル開発時は dashboard/ の1つ上(プロジェクト直下)、
# 公開用リポジトリでは app.py と同じ階層に sample-data/ を置く運用なので両方を探す。
SAMPLE_CANDIDATES = [
    os.path.join(HERE, "..", "sample-data", "latest.json"),
    os.path.join(HERE, "sample-data", "latest.json"),
]
DATA_PATH = "latest.json"

FOLDER_ALL = "__all__"
FOLDER_UNCLASSIFIED = "__unclassified__"

st.set_page_config(page_title="スクラップブックダッシュボード", page_icon=":material/bookmarks:", layout="wide")

# デザイン案(モックアップ)で承認した配色・タイポグラフィをここで再現する。
# ライト/ダークそれぞれの色をCSS変数として定義し、"自動"時はOSの配色設定(prefers-color-scheme)に
# 追従、ライト/ダークを明示選択した場合はそちらを強制する。
PALETTE = {
    "light": {
        "bg": "#e7ebe1", "surface": "#ffffff", "surface-2": "#edf1e7",
        "ink": "#1e2321", "ink-soft": "#565f5a", "ink-faint": "#6e766f",
        "border": "#c6d0bd", "border-strong": "#a3b096",
        "accent": "#235c4e", "accent-ink": "#ffffff",
        "accent-soft": "#cde5da", "accent-soft-ink": "#17453a",
    },
    "dark": {
        "bg": "#141715", "surface": "#1d211e", "surface-2": "#242926",
        "ink": "#e9ece7", "ink-soft": "#a7b0a9", "ink-faint": "#838b84",
        "border": "#333a35", "border-strong": "#454e47",
        "accent": "#6fbfae", "accent-ink": "#0d1210",
        "accent-soft": "#223531", "accent-soft-ink": "#a8e0d1",
    },
}


def _css_vars(palette: dict) -> str:
    return " ".join(f"--{k}: {v};" for k, v in palette.items())


def build_theme_css(theme_mode: str) -> str:
    if theme_mode == "ライト":
        root_block = f":root {{ {_css_vars(PALETTE['light'])} }}"
    elif theme_mode == "ダーク":
        root_block = f":root {{ {_css_vars(PALETTE['dark'])} }}"
    else:
        root_block = (
            f":root {{ {_css_vars(PALETTE['light'])} }}"
            f"@media (prefers-color-scheme: dark) {{ :root {{ {_css_vars(PALETTE['dark'])} }} }}"
        )
    return f"""
    <style>
      {root_block}

      html, body, .stApp {{ background: var(--bg); color: var(--ink); }}
      .stApp, .stApp p, .stApp span, .stApp label {{
        font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", -apple-system, "Segoe UI", Roboto, sans-serif;
      }}
      h1 {{
        font-family: "Hiragino Mincho ProN", "Yu Mincho", "Iowan Old Style", Georgia, serif !important;
        letter-spacing: 0.01em;
        color: var(--ink) !important;
      }}
      section[data-testid="stSidebar"] {{
        background: var(--surface-2);
        border-right: 1px solid var(--border);
      }}
      .block-container {{ padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1100px; }}

      div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: var(--surface);
        border-color: var(--border) !important;
        border-radius: 10px;
        margin-bottom: 0.35rem;
      }}
      hr {{ border-color: var(--border) !important; }}
      small, [data-testid="stCaptionContainer"] {{ color: var(--ink-faint) !important; }}

      button[kind="primary"], [data-testid="stBaseButton-primary"] {{
        background: var(--accent) !important;
        color: var(--accent-ink) !important;
        border: none !important;
      }}
      button[kind="secondary"], [data-testid="stBaseButton-secondary"] {{
        background: var(--surface) !important;
        color: var(--ink) !important;
        border: 1px solid var(--border-strong) !important;
      }}

      div[data-testid="stTextInput"] input {{
        background: var(--surface);
        color: var(--ink);
        border-color: var(--border-strong) !important;
      }}

      .sb-title {{
        display: block; font-weight: 600; font-size: 1.02rem; text-decoration: none; color: var(--ink);
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }}
      .sb-title:hover {{ text-decoration: underline; }}
      .sb-meta {{ color: var(--ink-faint); font-size: 0.82rem; text-align: right; }}
      .sb-excerpt {{
        color: var(--ink-soft); font-size: 0.88rem; margin-top: 2px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }}
    </style>
    """


# 配色トグル(サイドバー)の値をCSS生成より先に読む。key="theme_mode"のウィジェット自体は
# サイドバー描画時(このスクリプトの後半)に作るが、session_stateには前回の選択が
# 既に反映されているため、ここで先読みしても正しい値が取れる。
st.markdown(build_theme_css(st.session_state.get("theme_mode", "自動")), unsafe_allow_html=True)


# ---- GitHub 連携の設定 ----

def _repo_cfg(section: str) -> RepoConfig | None:
    """[section] の設定を返す。secrets が無い/不完全なら None(エラーではなく「まだ繋いでいない」状態)。"""
    try:
        cfg = st.secrets[section]
    except Exception:
        return None
    if not cfg.get("repo") or not cfg.get("token"):
        return None
    return RepoConfig(repo=cfg["repo"], token=cfg["token"], branch=cfg.get("branch", "main"))


def data_cfg() -> RepoConfig | None:
    return _repo_cfg("github_data")


def inbox_cfg() -> RepoConfig | None:
    return _repo_cfg("github_inbox")


@st.cache_data(ttl=60, show_spinner=False)
def fetch_data(cfg: RepoConfig) -> dict:
    data, _sha = get_json(cfg, DATA_PATH)
    if data is None:
        raise RuntimeError(
            "まだ記事データがアップロードされていません。スマホでAB Scrapbookを一度起動してください"
        )
    return data


def load_data() -> tuple[dict, str]:
    cfg = data_cfg()
    if cfg is not None:
        try:
            return fetch_data(cfg), "GitHub"
        except Exception as e:
            st.error(f"GitHubからの記事データ取得に失敗しました: {e}")
    for sample_path in SAMPLE_CANDIDATES:
        try:
            with open(sample_path, encoding="utf-8") as f:
                return json.load(f), "サンプルデータ(ローカル)"
        except FileNotFoundError:
            continue
    st.error(
        "GitHubの接続設定(secrets)がまだ登録されていません。\n\n"
        "デプロイ先アプリの **Settings → Secrets** に `[github_data]` "
        "(`repo`・`branch`・`token`)を設定してください。手順は `デプロイ手順.md` の「3. アプリを公開する」を参照してください。"
    )
    st.stop()


def queue_command(command: dict) -> bool:
    """`abscrapbook-inbox` に命令ファイルを1つ書き込む。設定が無ければ False(通信しない)。

    ファイル名の先頭はタイムスタンプ(ミリ秒)にして、Android側(`InboxProcessor`)が
    名前の昇順=古い順に処理できるようにしている。
    """
    cfg = inbox_cfg()
    if cfg is None:
        st.error(
            "PCからの保存・移動・削除には、ダッシュボードのSecretsに`[github_inbox]`の設定が必要です。"
            "詳しくは「デプロイ手順.md」を参照してください。"
        )
        return False
    filename = f"commands/{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json"
    try:
        put_json(cfg, filename, command, f"ダッシュボードからの指示: {command['type']}")
        return True
    except Exception as e:
        st.error(f"指示の送信に失敗しました: {e}")
        return False


def format_date(saved_at_millis: int) -> str:
    dt = datetime.fromtimestamp(saved_at_millis / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%m/%d")


# ---- 画面 ----

backup, source = load_data()
if source != "GitHub":
    st.info(
        f"現在は{source}を表示しています。本番の記事を出すには、GitHub連携の設定(secrets)が必要です。"
        "詳しくは「デプロイ手順.md」を参照してください。",
        icon=":material/info:",
    )

st.title("スクラップブック")
st.caption("AB Scrapbookに保存した記事を、PCのブラウザから閲覧・整理する")

for key, default in (
    ("pending_moves", {}),
    ("pending_deletes", set()),
    ("confirm_delete_id", None),
):
    if key not in st.session_state:
        st.session_state[key] = default

# ---- URLを保存(ブックマークレット経由の保存フロー) ----
# ブックマークレットは `?action=save&url=<現在のページURL>` を付けてこのページを開く。
# トークンはブラウザ側に一切渡らず、書き込みは常にこのサーバー側で行う。
qp = st.query_params
prefill_url = qp.get("url", "") if qp.get("action") == "save" else ""

with st.container(border=True):
    st.caption("URLを保存")
    save_col, btn_col = st.columns([5, 1], vertical_alignment="bottom")
    with save_col:
        url_value = st.text_input(
            "保存したいページのURL",
            value=prefill_url,
            placeholder="保存したいページのURLを貼り付け、またはブックマークレットで開く",
            key="save_url_input",
            label_visibility="collapsed",
        )
    with btn_col:
        save_clicked = st.button("保存する", type="primary", use_container_width=True)
    if prefill_url:
        st.caption(
            "ブックマークレットから開いたため、いま見ていたページのURLを自動で入力しました。"
            "トークン(合言葉)はこのページ自体には渡らず、保存はサーバー側で行われます。"
        )

if save_clicked:
    if not url_value.strip():
        st.warning("URLを入力してください")
    elif queue_command({"type": "save", "url": url_value.strip()}):
        st.success("保存の指示を送信しました。次にスマホでAB Scrapbookを開くと取り込まれます。")
        del st.session_state["save_url_input"]
        st.query_params.clear()
        st.rerun()

st.divider()


def do_move(article_id: int, target_key) -> None:
    target_collection_id = None if target_key == FOLDER_UNCLASSIFIED else target_key
    command = {"type": "move", "articleId": article_id, "collectionId": target_collection_id}
    if queue_command(command):
        st.session_state.pending_moves[article_id] = target_collection_id
        st.session_state.confirm_delete_id = None
        st.toast(f"「{folder_names.get(target_key, '?')}」への移動を送信しました", icon=":material/drive_file_move:")
        st.rerun()


def do_delete(article_id: int) -> None:
    if queue_command({"type": "delete", "articleId": article_id}):
        st.session_state.pending_deletes.add(article_id)
        st.session_state.confirm_delete_id = None
        st.toast("削除の指示を送信しました", icon=":material/delete:")
        st.rerun()


# 移動・削除の指示はまだGitHub上のlatest.jsonには反映されない(スマホが次に起動したときに反映される
# ため)。送信直後から一覧に反映して見えるよう、このセッション内だけで見た目上の状態を上書きする。
def effective_articles(data: dict) -> list[dict]:
    result = []
    for a in data["articles"]:
        if a["id"] in st.session_state.pending_deletes:
            continue
        collection_id = st.session_state.pending_moves.get(a["id"], a.get("collectionId"))
        result.append({**a, "collectionId": collection_id})
    result.sort(key=lambda a: a["savedAt"], reverse=True)
    return result


arts = effective_articles(backup)

folder_names: dict = {FOLDER_ALL: "すべて", FOLDER_UNCLASSIFIED: "未分類"}
for c in backup["collections"]:
    folder_names[c["id"]] = c["name"]
folder_options = [FOLDER_ALL, FOLDER_UNCLASSIFIED] + [c["id"] for c in backup["collections"]]

folder_counts = {k: 0 for k in folder_options}
for a in arts:
    folder_counts[FOLDER_ALL] += 1
    key = a["collectionId"] if a["collectionId"] in folder_counts else FOLDER_UNCLASSIFIED
    folder_counts[key] += 1

tag_counts: dict[str, int] = {}
for a in arts:
    for t in a.get("tags", []):
        tag_counts[t] = tag_counts.get(t, 0) + 1
tag_options = sorted(tag_counts.keys(), key=lambda t: (-tag_counts[t], t))

with st.sidebar:
    st.radio(
        "配色",
        options=["自動", "ライト", "ダーク"],
        key="theme_mode",
        horizontal=True,
    )
    st.divider()

    query = st.text_input("検索", placeholder="タイトル・要約を検索", label_visibility="collapsed")

    st.subheader("フォルダ")
    selected_folder = st.radio(
        "フォルダ",
        options=folder_options,
        format_func=lambda k: f"{folder_names.get(k, '?')} ({folder_counts.get(k, 0)})",
        key="selected_folder",
        label_visibility="collapsed",
    )

    st.subheader("タグ")
    selected_tags = st.multiselect(
        "タグ",
        options=tag_options,
        format_func=lambda t: f"{t} ({tag_counts[t]})",
        placeholder="タグで絞り込み(入力して検索)",
        key="selected_tags",
        label_visibility="collapsed",
    )
    st.caption(f"タグは{len(tag_options)}件あります。入力すると絞り込めます。")


def matches(a: dict) -> bool:
    if selected_folder == FOLDER_UNCLASSIFIED:
        if a["collectionId"] is not None:
            return False
    elif selected_folder != FOLDER_ALL:
        if a["collectionId"] != selected_folder:
            return False
    if selected_tags and not set(selected_tags).issubset(set(a.get("tags", []))):
        return False
    if query.strip():
        haystack = " ".join([a["title"], a.get("summary") or "", a.get("excerpt") or ""]).lower()
        if query.strip().lower() not in haystack:
            return False
    return True


visible = [a for a in arts if matches(a)]

header_title = "すべての記事" if selected_folder == FOLDER_ALL else folder_names.get(selected_folder, "?")
st.markdown(f"**{header_title}**")

# 記事が数千件規模になると、1件ごとにポップオーバー等の重いウィジェットを持つカードを
# 一度に全件描画すると画面が固まったようになるため、ページ送りで表示件数を区切る。
ARTICLES_PER_PAGE = 50
filter_key = (selected_folder, tuple(sorted(selected_tags)), query.strip())
if st.session_state.get("_filter_key") != filter_key:
    st.session_state["_filter_key"] = filter_key
    st.session_state["page"] = 1

total_pages = max(1, -(-len(visible) // ARTICLES_PER_PAGE))
page = min(max(st.session_state.get("page", 1), 1), total_pages)
st.session_state["page"] = page
page_start = (page - 1) * ARTICLES_PER_PAGE
page_articles = visible[page_start : page_start + ARTICLES_PER_PAGE]

st.caption(f"{len(arts)}件中 {len(visible)}件が該当({page}/{total_pages}ページ目、{len(page_articles)}件を表示)")

if not visible:
    st.info("条件に一致する記事がありません。")


def render_pagination(position: str) -> None:
    if total_pages <= 1:
        return
    prev_col, mid_col, next_col = st.columns([1, 2, 1])
    with prev_col:
        if st.button("← 前へ", disabled=page <= 1, use_container_width=True, key=f"prev_{position}_{page}"):
            st.session_state["page"] = page - 1
            st.rerun()
    with mid_col:
        st.markdown(
            f'<div style="text-align:center; padding-top:0.4rem;">{page} / {total_pages} ページ</div>',
            unsafe_allow_html=True,
        )
    with next_col:
        if st.button("次へ →", disabled=page >= total_pages, use_container_width=True, key=f"next_{position}_{page}"):
            st.session_state["page"] = page + 1
            st.rerun()


render_pagination("top")

move_options = [FOLDER_UNCLASSIFIED] + [c["id"] for c in backup["collections"]]

for a in page_articles:
    with st.container(border=True):
        cols = st.columns([7, 2, 1], vertical_alignment="center")

        with cols[0]:
            st.markdown(
                f'<a class="sb-title" href="{escape(a["url"])}" target="_blank" rel="noopener">{escape(a["title"])}</a>',
                unsafe_allow_html=True,
            )

        with cols[1]:
            meta_parts = [a["siteName"]] if a.get("siteName") else []
            meta_parts.append(format_date(a["savedAt"]))
            st.markdown(f'<div class="sb-meta">{escape(" ・ ".join(meta_parts))}</div>', unsafe_allow_html=True)

        with cols[2]:
            with st.popover("⋯", use_container_width=True):
                current = a["collectionId"] if a["collectionId"] is not None else FOLDER_UNCLASSIFIED

                st.caption("移動")
                target = st.selectbox(
                    "移動先",
                    options=move_options,
                    index=move_options.index(current) if current in move_options else 0,
                    format_func=lambda k: folder_names.get(k, "?"),
                    key=f"move_{a['id']}",
                    label_visibility="collapsed",
                )
                if target != current:
                    st.button(
                        f"「{folder_names.get(target, '?')}」に移動する",
                        key=f"move_btn_{a['id']}",
                        use_container_width=True,
                        on_click=do_move,
                        args=(a["id"], target),
                    )

                st.divider()
                if st.session_state.confirm_delete_id == a["id"]:
                    st.button(
                        "本当に削除しますか?",
                        key=f"del_confirm_{a['id']}",
                        type="primary",
                        use_container_width=True,
                        on_click=do_delete,
                        args=(a["id"],),
                    )
                else:
                    if st.button("削除", key=f"del_{a['id']}", use_container_width=True):
                        st.session_state.confirm_delete_id = a["id"]
                        st.rerun()

        excerpt = a.get("summary") or a.get("excerpt") or ""
        if excerpt:
            # summary はAI要約(最大3行の箇条書き、AB Scrapbook側のTHREE_BULLETS出力)、
            # excerpt は1行要約(ONE_BULLET)。複数行あるときは1行ずつ表示する。
            lines = [ln.strip() for ln in excerpt.splitlines() if ln.strip()]
            if len(lines) > 1:
                html = "".join(f'<div class="sb-excerpt">・{escape(ln)}</div>' for ln in lines)
            else:
                html = f'<div class="sb-excerpt">{escape(lines[0] if lines else excerpt)}</div>'
            st.markdown(html, unsafe_allow_html=True)

render_pagination("bottom")
