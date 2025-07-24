
import streamlit as st
import pandas as pd
import openai
import requests

# APIキー読み込み（Streamlit Cloudでは secrets.toml で管理）
openai.api_key = st.secrets["openai_api_key"]
GOOGLE_API_KEY = st.secrets["google_api_key"]
GOOGLE_CX = st.secrets["google_cse_id"]

client = openai.OpenAI(api_key=openai.api_key)

st.title("メーカー・ブランド補完ツール（Google検索＋GPT）")

uploaded_file = st.file_uploader("AI補完対象ファイル（CSV）をアップロードしてください", type=["csv"])

def google_search(query, api_key, cx):
    url = f"https://www.googleapis.com/customsearch/v1"
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": 5,
        "lr": "lang_ja"
    }
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        data = res.json()
        results = []
        for item in data.get("items", []):
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            results.append(f"{title}\n{snippet}\n{link}")
        return "\n\n".join(results)
    except Exception as e:
        return f"[GoogleSearchError] {e}"

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    st.write("アップロードされたデータ", df)

    brand_list, maker_list, reason_list, source_list = [], [], [], []

    for idx, row in df.iterrows():
        query = f"{row['ユニーク名']} {row['型番']} {row['JANコード']}".strip()
        search_summary = google_search(query, GOOGLE_API_KEY, GOOGLE_CX)

        prompt = f"""
以下は商品「{row['ユニーク名']}」に関するWeb検索結果の要約です：

{search_summary}

この情報から推定されるブランド名とメーカー名を以下の形式で答えてください：

ブランド：
メーカー：
理由：
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "あなたは商品分類の専門家です。正確な判断が難しい場合は空欄のままにしてください。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            content = response.choices[0].message.content
        except Exception as e:
            content = f"[GPTError] {e}"

        # 結果のパース（単純分割）
        brand, maker, reason = "", "", ""
        for line in content.splitlines():
            if line.startswith("ブランド："):
                brand = line.replace("ブランド：", "").strip()
            elif line.startswith("メーカー："):
                maker = line.replace("メーカー：", "").strip()
            elif line.startswith("理由："):
                reason = line.replace("理由：", "").strip()

        brand_list.append(brand)
        maker_list.append(maker)
        reason_list.append(reason)
        source_list.append(search_summary[:300])  # 最初の300文字だけ保持

    df["AI_ブランド"] = brand_list
    df["AI_メーカー"] = maker_list
    df["AI_理由"] = reason_list
    df["検索サマリ"] = source_list

    st.download_button(
        label="結果をCSVでダウンロード",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="AI補完結果_検索付き.csv",
        mime="text/csv"
    )
