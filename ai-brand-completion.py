import streamlit as st
import pandas as pd
import openai
import requests
import re

# シークレットキー読み込み
openai.api_key = st.secrets["openai_api_key"]
GOOGLE_API_KEY = st.secrets["google_api_key"]
GOOGLE_CX = st.secrets["google_cse_id"]

client = openai.OpenAI(api_key=openai.api_key)

st.title("メーカー・ブランド補完ツール（完全強化版）")

uploaded_file = st.file_uploader("AI補完対象ファイル（CSV）をアップロードしてください", type=["csv"])

# --- Google検索処理
def google_search(query, api_key, cx):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": 5,
        "lr": "lang_ja"
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        results = []
        urls = []
        for item in data.get("items", []):
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            results.append(f"{title}\n{snippet}\n{link}")
            urls.append(link)
        return "\n\n".join(results), ", ".join(urls[:3])
    except Exception as e:
        return f"[GoogleSearchError] {e}", ""

# --- 安全に列を取り出す
def get_safe(row, col):
    return row[col] if col in row and pd.notnull(row[col]) else ""

# --- パース強化
def parse_gpt_output(text):
    brand, maker, reason = "", "", ""
    for line in text.splitlines():
        b_match = re.match(r"^\s*(ブランド名|ブランド|ﾌﾞﾗﾝﾄﾞ)[：:]\s*(.*)$", line)
        m_match = re.match(r"^\s*(メーカー名|メーカー|ﾒｰｶｰ)[：:]\s*(.*)$", line)
        r_match = re.match(r"^\s*(理由|補足|根拠)[：:]\s*(.*)$", line)
        if b_match: brand = b_match.group(2).strip()
        elif m_match: maker = m_match.group(2).strip()
        elif r_match: reason = r_match.group(2).strip()
    return brand, maker, reason

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    st.write("アップロードされたデータ", df)

    # 必須列チェック
    required_cols = ["ユニーク名", "型番", "JANコード"]
    if not all(col in df.columns for col in required_cols):
        st.error(f"以下の列が不足しています: {set(required_cols) - set(df.columns)}")
        st.stop()

    # 処理用リスト
    brand_list, maker_list, reason_list = [], [], []
    query_list, url_list, source_list = [], [], []
    error_rows = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, row in df.iterrows():
        status_text.text(f"処理中: {idx+1} / {len(df)}")
        progress_bar.progress((idx + 1) / len(df))

        try:
            # クエリ作成
            query_parts = [
                get_safe(row, 'ユニーク名'),
                get_safe(row, '型番'),
                get_safe(row, 'JANコード'),
                get_safe(row, '管理カテゴリー大大'),
                get_safe(row, '管理カテゴリー大'),
                get_safe(row, '管理カテゴリー中'),
                get_safe(row, '管理カテゴリー小'),
                get_safe(row, '商品カテゴリー大大'),
                get_safe(row, '商品カテゴリー大'),
                get_safe(row, '商品カテゴリー中'),
                get_safe(row, '商品カテゴリー小')
            ]
            query = " ".join([q for q in query_parts if q.strip() != ""])
            query_list.append(query)

            search_summary, urls = google_search(query, GOOGLE_API_KEY, GOOGLE_CX)
            url_list.append(urls)

            prompt = f"""
以下は商品「{get_safe(row, 'ユニーク名')}」に関するWeb検索結果の要約です：

{search_summary}

この情報から推定されるブランド名とメーカー名を以下の形式で答えてください：

ブランド：
メーカー：
理由：
            """

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "あなたは商品分類の専門家です。正確な判断が難しい場合は空欄のままにしてください。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            content = response.choices[0].message.content
            brand, maker, reason = parse_gpt_output(content)

        except Exception as e:
            brand = maker = reason = ""
            content = f"[Error] {e}"
            error_rows.append(row.to_dict())

        brand_list.append(brand)
        maker_list.append(maker)
        reason_list.append(reason)
        source_list.append(search_summary[:300] if isinstance(search_summary, str) else "")

    # データ統合
    df["検索クエリ"] = query_list
    df["AI_ブランド"] = brand_list
    df["AI_メーカー"] = maker_list
    df["AI_理由"] = reason_list
    df["検索サマリ"] = source_list
    df["参照URL"] = url_list

    # 出力ボタン
    st.download_button(
        label="✅ 補完結果CSVをダウンロード",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="AI補完結果_完全版.csv",
        mime="text/csv"
    )

    if error_rows:
        err_df = pd.DataFrame(error_rows)
        st.warning(f"⚠️ エラー発生行数: {len(error_rows)}件（別途ダウンロード可能）")
        st.download_button(
            label="⚠️ エラー行CSVをダウンロード",
            data=err_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="AI補完エラー行.csv",
            mime="text/csv"
        )
