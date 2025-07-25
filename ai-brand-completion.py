import streamlit as st
import pandas as pd
import openai
import requests
import base64
import re
import uuid
from datetime import datetime
import io
import time
import os
import tempfile

# === Secrets ===
openai.api_key = st.secrets["openai_api_key"]
GOOGLE_API_KEY = st.secrets["google_api_key"]
GOOGLE_CX = st.secrets["google_cse_id"]
GITHUB_TOKEN = st.secrets["github_token"]
GITHUB_REPO = st.secrets["github_repo"]
EXCLUDE_PATH = st.secrets["exclude_path"]

client = openai.OpenAI(api_key=openai.api_key)
st.set_page_config(layout="wide")
st.title("🧠 メーカー・ブランド補完ツール（コード整合性対応）")

# === 補助関数 ===
def generate_unique_filename(base: str, ext: str = "csv"):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{base}_{ts}_{uid}.{ext}"

def create_temp_logfile():
    tmp_dir = tempfile.gettempdir()
    fname = generate_unique_filename("ai_completion_log", "txt")
    return os.path.join(tmp_dir, fname)

if "logs" not in st.session_state:
    st.session_state.logs = []

def log(msg):
    stamp = datetime.now().strftime("[%H:%M:%S]")
    st.session_state.logs.append(f"{stamp} {msg}")

def call_gpt_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            res = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": (
                        "あなたは商品分類の専門家です。以下のルールに従って、ブランド名とメーカー名を正しく推定してください：\n"
                        "- ブランドは製品名やサービス名を指し、必ずいずれかのメーカーに属している。\n"
                        "- メーカーは製造・販売元であり、ブランドを保有する企業名です。\n"
                        "- 色名・カテゴリ・素材（例：ホワイト、紙、洗剤等）はブランドとみなしてはいけません。\n"
                        "- ブランドが不明な場合は『ブランド：該当なし』としてください。\n"
                        "ブランドとメーカーは正確に対応させてください。"
                    )},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            return res.choices[0].message.content
        except Exception as e:
            log(f"GPTリトライ{attempt+1}回目失敗: {e}")
            time.sleep(2 ** attempt)
    raise RuntimeError("GPT API呼び出し失敗")

def google_search_with_retry(query, exclude_domains, max_retries=3):
    for attempt in range(max_retries):
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CX,
                "q": query,
                "num": 5,
                "lr": "lang_ja"
            }
            res = requests.get(url, params=params, timeout=15)
            res.raise_for_status()
            data = res.json()
            results, urls = [], []
            for item in data.get("items", []):
                link = item.get("link", "")
                if any(x in link for x in exclude_domains):
                    continue
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                results.append(f"{title}\n{snippet}\n{link}")
                urls.append(link)
            return "\n\n".join(results), ", ".join(urls[:3])
        except Exception as e:
            log(f"Google検索リトライ{attempt+1}回目失敗: {e}")
            time.sleep(2 ** attempt)
    return "[GoogleSearchError] 全リトライ失敗", ""
# === GitHub除外ドメイン読み込み ===
@st.cache_data
def load_exclude_list_from_github():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{EXCLUDE_PATH}"
    try:
        df = pd.read_csv(url)
        return df.iloc[:, 0].dropna().tolist()
    except Exception as e:
        st.warning(f"❌ 除外ドメインの読み込みに失敗しました: {e}")
        return []

def upload_to_github(content_str: str):
    get_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{EXCLUDE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    sha = None
    try:
        res = requests.get(get_url, headers=headers)
        if res.status_code == 200:
            sha = res.json().get("sha")
    except:
        pass

    encoded = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")
    payload = {
        "message": "更新: 除外ドメインリスト",
        "content": encoded,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    put_res = requests.put(get_url, headers=headers, json=payload)
    return put_res.status_code, put_res.json()

# === サイドバー：除外リスト管理 ===
st.sidebar.header("🛡️ 除外ドメイン管理")
exclude_list = load_exclude_list_from_github()
st.sidebar.code("\n".join(exclude_list) or "（除外リストなし）")

uploaded_exclude = st.sidebar.file_uploader("📤 除外ドメインリスト（CSV）", type=["csv"])
if uploaded_exclude:
    content = uploaded_exclude.getvalue().decode("utf-8")
    if st.sidebar.button("🚀 GitHubへ保存"):
        status, resp = upload_to_github(content)
        if status in (200, 201):
            st.sidebar.success("✅ 保存成功。リロードで反映されます")
            st.cache_data.clear()
        else:
            st.sidebar.error(f"❌ 保存失敗: {resp}")

# === サイドバー：ブランド・メーカーコード付きマスタ読込 ===
st.sidebar.header("📚 コード付きマスタアップロード")
uploaded_master = st.sidebar.file_uploader("ブランドマスタ（CSV）", type=["csv"])
brand_dict = {}
if uploaded_master:
    try:
        df_master = pd.read_csv(uploaded_master, dtype=str).fillna("")
        valid_rows = 0
        for _, row in df_master.iterrows():
            brand = row.get("ブランド名", "").strip()
            if not brand:
                continue
            bcd = row.get("ブランドコード", "").strip()
            mcd = row.get("メーカーコード", "").strip()
            mkr = row.get("メーカー名", "").strip()
            brand_dict[brand] = (bcd, mcd, mkr)
            valid_rows += 1
        st.sidebar.success(f"✅ 読み込み成功（有効ブランド数: {valid_rows}）")
    except Exception as e:
        st.sidebar.error(f"❌ マスタ読み込みエラー: {e}")
def get_safe(row, col):
    return row[col] if col in row and pd.notnull(row[col]) else ""

def parse_gpt_output(text):
    brand, maker, reason = "", "", ""
    for line in text.splitlines():
        b = re.match(r"^\s*(ブランド名|ブランド|ﾌﾞﾗﾝﾄﾞ)[：:]\s*(.*)$", line)
        m = re.match(r"^\s*(メーカー名|メーカー|ﾒｰｶｰ)[：:]\s*(.*)$", line)
        r = re.match(r"^\s*(理由|補足|根拠)[：:]\s*(.*)$", line)
        if b: brand = b.group(2).strip()
        elif m: maker = m.group(2).strip()
        elif r: reason = r.group(2).strip()
    return brand, maker, reason

uploaded_file = st.file_uploader("📄 AI補完対象ファイル（CSV）", type=["csv"])

if st.button("🔄 リセット"):
    st.session_state.pop("result_df", None)
    st.session_state.pop("error_df", None)
    st.experimental_rerun()

if uploaded_file and "result_df" not in st.session_state:

    if not brand_dict:
        st.error("⚠️ ブランド・メーカーのマスタCSVをアップロードしてください。")
        st.stop()

    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    st.write("アップロードされたデータ", df)

    required = ["ユニーク名", "型番", "JANコード"]
    if not all(c in df.columns for c in required):
        st.error(f"必要列が不足: {set(required) - set(df.columns)}")
        st.stop()

    b_list, m_list, r_list, q_list, s_list, u_list = [], [], [], [], [], []
    bcd_list, mcd_list, mmk_list, match_flag_list = [], [], [], []
    err_rows = []

    progress = st.progress(0)
    status = st.empty()

    for i, row in df.iterrows():
        status.text(f"{i+1}/{len(df)} 処理中…")
        progress.progress((i + 1) / len(df))

        query_parts = [
            get_safe(row, 'ユニーク名'), get_safe(row, '型番'), get_safe(row, 'JANコード'),
            get_safe(row, '管理カテゴリー大大'), get_safe(row, '管理カテゴリー大'),
            get_safe(row, '管理カテゴリー中'), get_safe(row, '管理カテゴリー小'),
            get_safe(row, '商品カテゴリー大大'), get_safe(row, '商品カテゴリー大'),
            get_safe(row, '商品カテゴリー中'), get_safe(row, '商品カテゴリー小')
        ]
        query = " ".join([q for q in query_parts if q.strip()])
        q_list.append(query)

        log(f"処理開始: {query}")

        try:
            summary, urls = google_search_with_retry(query, exclude_list)
            prompt = f"""
以下は商品「{get_safe(row, 'ユニーク名')}」に関するWeb検索結果の要約です：

{summary}

この情報から推定されるブランド名とメーカー名を以下の形式で答えてください：

ブランド：
メーカー：
理由：
"""
            res_text = call_gpt_with_retry(prompt)
            log(f"→ GPT回答先頭行: {res_text.splitlines()[0] if res_text else 'なし'}")
            brand, maker, reason = parse_gpt_output(res_text)
        except Exception as e:
            log(f"❌ 処理失敗: {e}")
            brand, maker, reason = "", "", ""
            summary, urls = "", ""
            err_rows.append(row.to_dict())

        if brand in brand_dict:
            bcd, mcd, mmk = brand_dict[brand]
            match_flag = "〇" if maker == mmk else "×"
            mas_br = brand  # AIが出したブランドがマスタに存在しているので、それをマスタブランド名とする
        else:
            bcd, mcd, mmk, match_flag, mas_br = "", "", "", "（マスタなし）", ""

        b_list.append(brand)
        m_list.append(maker)
        r_list.append(reason)
        s_list.append(summary[:300])
        u_list.append(urls)
        bcd_list.append(bcd)
        mcd_list.append(mcd)
        mmk_list.append(mmk)
        match_flag_list.append(match_flag)
        mas_br_list.append(mas_br)  # ★ 追加

    df["検索クエリ"] = q_list
    df["AI_ブランド"] = b_list
    df["AI_メーカー"] = m_list
    df["AI_理由"] = r_list
    df["検索サマリ"] = s_list
    df["参照URL"] = u_list
    df["ブランドコード"] = bcd_list
    df["メーカーコード"] = mcd_list
    df["マスタブランド名"] = mas_br_list    # ★ 追加
    df["マスタメーカー名"] = mmk_list
    df["ブランド⇄メーカー整合性"] = match_flag_list

    st.session_state.result_df = df
    st.session_state.error_df = pd.DataFrame(err_rows)

if "result_df" in st.session_state:
    st.markdown("### ✅ 補完結果")
    st.dataframe(st.session_state.result_df, use_container_width=True)

    result_filename = generate_unique_filename("AI補完結果_コード対応", "csv")
    st.download_button(
        "📥 補完結果CSVをダウンロード",
        st.session_state.result_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=result_filename,
        mime="text/csv"
    )

if "error_df" in st.session_state and not st.session_state.error_df.empty:
    st.markdown("### ⚠️ エラー発生行")
    st.dataframe(st.session_state.error_df, use_container_width=True)

    error_filename = generate_unique_filename("AI補完エラー行", "csv")
    st.download_button(
        "⚠️ エラー行CSVをダウンロード",
        st.session_state.error_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=error_filename,
        mime="text/csv"
    )

# === サイドバーに処理ログを表示（フォント小・色分け） ===
if st.session_state.logs:
    st.sidebar.markdown("### 🧾 処理ログ")
    with st.sidebar.expander("ログを表示"):
        for line in st.session_state.logs:
            if "❌" in line or "失敗" in line:
                color = "red"
            elif "〇" in line or "✅" in line:
                color = "green"
            else:
                color = "black"

            st.markdown(
                f"<div style='font-size:12px; color:{color}; font-family:monospace'>{line}</div>",
                unsafe_allow_html=True
            )

    # ログファイルを保存してDLリンクを表示
    log_file_path = create_temp_logfile()
    with open(log_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(st.session_state.logs))

    st.sidebar.download_button(
        "📝 ログファイルをDL",
        data=open(log_file_path, "rb").read(),
        file_name=os.path.basename(log_file_path),
        mime="text/plain"
    )
