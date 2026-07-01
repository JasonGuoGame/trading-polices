import streamlit as st
import chromadb
import requests
import json
import pandas as pd
from sqlalchemy import create_engine, text

# --- 配置 ---
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')
client = chromadb.PersistentClient(path="./v_db_shuimu")
collection = client.get_or_create_collection(name="market_sentiment")

# --- 辅助函数：获取今日热词 ---
def get_hot_topics_from_sql():
    sql = """
    SELECT topic, COUNT(*) as cnt 
    FROM forum_post 
    WHERE created_time >= CURDATE() AND topic != '全市场'
    GROUP BY topic ORDER BY cnt DESC LIMIT 5
    """
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
            return ", ".join([f"{row['topic']}({row['cnt']}次)" for _, row in df.iterrows()])
    except:
        return "暂无数据"

# --- 核心 UI ---
st.set_page_config(page_title="水木量化分析专家", layout="wide")
st.title("🛡️ 水木论坛舆情 RAG 实战 UI")

with st.sidebar:
    st.header("📊 实时看板")
    hot_list = get_hot_topics_from_sql()
    st.write(f"今日全场讨论最热：\n**{hot_list}**")
    st.divider()
    
    # 动态作者列表
    authors = pd.read_sql("SELECT DISTINCT author FROM forum_post WHERE author != '' ORDER BY id DESC LIMIT 50", engine)
    selected_author = st.selectbox("🎯 定向分析作者", ["全场"] + authors['author'].tolist())

# 对话逻辑
if prompt := st.chat_input("问我：'beinghalf推荐了什么？' 或 '均胜电子怎么看？'"):
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.spinner("AI 正在深度研读帖子..."):
        # 1. 向量检索
        query_embedding = requests.post("http://127.0.0.1:11434/api/embeddings", 
                                       json={"model": "bge-m3:latest", "prompt": prompt}).json()['embedding']
        
        search_params = {"query_embeddings": [query_embedding], "n_results": 15}
        if selected_author != "全场":
            search_params["where"] = {"author": selected_author}
            
        results = collection.query(**search_params)
        
        # 2. 拼接上下文
        context = ""
        for i in range(len(results['documents'][0])):
            auth = results['metadatas'][0][i].get('author', '未知')
            context += f"【作者:{auth}】说: {results['documents'][0][i]}\n---\n"

        # 3. 构造给 R1 的专业 Prompt
        system_prompt = f"""
        你是一个资深的A股量化策略分析师。
        目前全场最热的板块统计如下：{hot_list}
        
        请根据以下检索到的水木论坛原始讨论片段，回答用户的提问："{prompt}"。
        
        ### 参考资料 ###
        {context}
        
        ### 任务要求 ###
        - 如果用户问特定作者（如beinghalf），请从资料中提取他提到的所有股票，并总结他的操作风格。
        - 如果用户问特定股票（如均胜电子），请归纳大家对它的利好和利空分析，并关注是否提到了技术位。
        - 如果资料中没有直接答案，请根据资料中的蛛丝马迹给出推测，或者诚实回答未发现。
        """

        # 4. 调用 R1 (流式)
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            res = requests.post("http://127.0.0.1:11434/api/generate", 
                               json={"model": "deepseek-r1:8b", "prompt": system_prompt, "stream": True}, 
                               stream=True)
            
            for line in res.iter_lines():
                if line:
                    chunk = json.loads(line.decode('utf-8'))
                    full_response += chunk.get('response', '')
                    response_placeholder.markdown(full_response + "▌")
            
            response_placeholder.markdown(full_response)