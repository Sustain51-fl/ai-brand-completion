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
st.title("🧠 メーカー・ブランド補完ツール（除外管理統合版）")

# === GitHub除外ドメイン管理 ===
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

# === Google検索（除外適用） ===
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

# === サイドバー：除外ドメイン管理 ===
st.sidebar.header("🛡 除外ドメイン管理")

exclude_list = load_exclude_list_from_github()
st.sidebar.write("現在の除外リスト:")
st.sidebar.code("\n".join(exclude_list) or "（なし）")

uploaded_exclude = st.sidebar.file_uploader("📤 新しい除外リストCSV", type=["csv"])
if uploaded_exclude:
    content = uploaded_exclude.getvalue().decode("utf-8")
    if st.sidebar.button("🚀 GitHubへ保存"):
        status, resp = upload_to_github(content)
        if status in (200, 201):
            st.sidebar.success("保存成功。リロードで反映されます")
            st.cache_data.clear()
        else:
            st.sidebar.error(f"保存失敗: {resp}")

# === メイン処理：ブランド補完 ===
uploaded_file = st.file_uploader("📄 AI補完対象ファイル（CSV）", type=["csv"])
if uploaded_file:
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    st.write("アップロードされたデータ", df)

    required = ["ユニーク名", "型番", "JANコード"]
    if not all(c in df.columns for c in required):
        st.error(f"必要列が不足: {set(required) - set(df.columns)}")
        st.stop()

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

    st.download_button("📥 補完結果CSVをダウンロード", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="AI補完結果_統合版.csv", mime="text/csv")

    if err_rows:
        df_err = pd.DataFrame(err_rows)
        st.warning(f"⚠️ エラー行数: {len(err_rows)} 件")
        st.download_button("⚠️ エラー行CSVをダウンロード", df_err.to_csv(index=False).encode("utf-8-sig"),
                           file_name="AI補完エラー行.csv", mime="text/csv")
