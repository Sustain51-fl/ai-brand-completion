
import streamlit as st
import pandas as pd
import openai

# v1.x 以降のクライアント初期化
client = openai.OpenAI(api_key=st.secrets["openai_api_key"])

st.title("メーカー・ブランド補完ツール（GPT-4o＋Streamlit）")

uploaded_file = st.file_uploader("AI補完対象ファイル（CSV）をアップロードしてください", type=["csv"])

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")

    st.write("アップロードされたデータ", df)

    results = []
    for idx, row in df.iterrows():
        query = f"{row['ユニーク名']} {row['型番']} {row['JANコード']}".strip()
        prompt = f"""
以下の商品情報から、ブランド名とメーカー名をできる限り正確に推定してください。

ユニーク名：{row['ユニーク名']}
型番：{row['型番']}
JANコード：{row['JANコード']}

【出力形式】
ブランド：
メーカー：
理由：
        """

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "あなたは商品分類の専門家です。正確性を重視して、あいまいな場合は無理に推定せず空欄を維持してください。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            content = response.choices[0].message.content
        except Exception as e:
            content = f"[ERROR] {e}"

        results.append(content)

    df["AI補完結果"] = results

    st.download_button(
        label="結果をCSVでダウンロード",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="AI補完結果付き.csv",
        mime="text/csv"
    )
