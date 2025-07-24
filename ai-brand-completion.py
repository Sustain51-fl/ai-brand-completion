import streamlit as st
import pandas as pd
import openai
import requests
import base64
import re

# === Secrets ===
openai.api_key = st.secrets["openai_api_key"]
GOOGLE_API_KEY = st.secrets["google_api_key"]
GOOGLE_CX = st.secrets["google_cse_id"]
GITHUB_TOKEN = st.secrets["github_token"]
GITHUB_REPO = st.secrets["github_repo"]
EXCLUDE_PATH = st.secrets["exclude_path"]

client = openai.OpenAI(api_key=openai.api_key)

st.set_page_config(layout="wide")
st.title("🧠 メーカー・ブランド補完ツール（再処理防止＋リセット対応）")

# === 除外リストの読み込み（GitHubからrawで）
@st.cache_data
def load_exclude_list_from_github():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{EXCLUDE_PATH}"
    try:
        df = pd.read_csv(url)
        return df.iloc[:, 0].dropna().tolist()
    except Exception as e:
        st.warning(f"❌ 除外ドメインの読み込みに失敗しました: {e}")
        return []

def google_search(query, exclude_domains):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX,
        "q": query,
        "num": 5,
        "lr": "lang_ja"
    }
    try:
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
        return f"[GoogleSearchError] {e}", ""

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

# === UI: ファイルアップロード＋リセットボタン
uploaded_file = st.file_uploader("📄 AI補完対象ファイル（CSV）", type=["csv"])

if st.button("🔄 リセット"):
    st.session_state.pop("result_df", None)
    st.session_state.pop("error_df", None)
    st.experimental_rerun()

# === 初回処理 or セッションに結果があれば表示
if uploaded_file and "result_df" not in st.session_state:
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    st.write("アップロードされたデータ", df)

    required = ["ユニーク名", "型番", "JANコード"]
    if not all(c in df.columns for c in required):
        st.error(f"必要列が不足: {set(required) - set(df.columns)}")
        st.stop()

    exclude_list = load_exclude_list_from_github()

    b_list, m_list, r_list, q_list, s_list, u_list, err_rows = [], [], [], [], [], [], []
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

        try:
            summary, urls = google_search(query, exclude_list)
            prompt = f"""
以下は商品「{get_safe(row, 'ユニーク名')}」に関するWeb検索結果の要約です：

{summary}

この情報から推定されるブランド名とメーカー名を以下の形式で答えてください：

ブランド：
メーカー：
理由：
            """
            res = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "あなたは商品分類の専門家です。判断が困難な場合は空欄で構いません。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            content = res.choices[0].message.content
            brand, maker, reason = parse_gpt_output(content)
        except Exception as e:
            brand, maker, reason = "", "", ""
            err_rows.append(row.to_dict())
            summary, urls = "", ""

        b_list.append(brand)
        m_list.append(maker)
        r_list.append(reason)
        s_list.append(summary[:300])
        u_list.append(urls)

    df["検索クエリ"] = q_list
    df["AI_ブランド"] = b_list
    df["AI_メーカー"] = m_list
    df["AI_理由"] = r_list
    df["検索サマリ"] = s_list
    df["参照URL"] = u_list

    st.session_state.result_df = df
    st.session_state.error_df = pd.DataFrame(err_rows)

# === 出力：セッションに保持されたデータからダウンロード
if "result_df" in st.session_state:
    st.download_button(
        "📥 補完結果CSVをダウンロード",
        st.session_state.result_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="AI補完結果_統合版.csv",
        mime="text/csv"
    )

if "error_df" in st.session_state and not st.session_state.error_df.empty:
    st.warning(f"⚠️ エラー行数: {len(st.session_state.error_df)} 件")
    st.download_button(
        "⚠️ エラー行CSVをダウンロード",
        st.session_state.error_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="AI補完エラー行.csv",
        mime="text/csv"
    )
