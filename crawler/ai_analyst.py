import os
import requests
import chromadb
from sqlalchemy import create_engine, text

# 1. 禁用代理（确保 Ollama 连接稳定）
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''

# 2. 初始化 ChromaDB
client = chromadb.PersistentClient(path="./v_db_shuimu")
collection = client.get_or_create_collection(name="market_sentiment")

def get_embedding(text_input):
    """获取问题的向量"""
    url = "http://127.0.0.1:11434/api/embeddings"
    payload = {"model": "bge-m3:latest", "prompt": text_input[:800]}
    res = requests.post(url, json=payload, proxies={'http': None, 'https': None})
    return res.json()['embedding']

def ask_deepseek_r1(prompt):
    """调用本地 DeepSeek R1 进行推理"""
    url = "http://127.0.0.1:11434/api/generate"
    payload = {
        "model": "deepseek-r1:8b", # 使用你本地的 8b 模型
        "prompt": prompt,
        "stream": False
    }
    print("🧠 DeepSeek R1 正在思考中 (可能会有思考过程)...")
    res = requests.post(url, json=payload, timeout=120, proxies={'http': None, 'https': None})
    return res.json()['response']

def smart_review(query):
    print(f"🔍 正在检索与 '{query}' 相关的帖子...")
    
    # A. 将问题向量化并检索最相关的 10 条帖子
    query_vec = get_embedding(query)
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=10
    )
    
    
    # B. 拼接上下文
    context = "\n".join(results['documents'][0])
    print(context)
    # C. 构造 Prompt（解决你之前的迷茫：为什么低开？）
    full_prompt = f"""
    你是一个资深的A股量化交易专家。以下是从水木论坛检索到的关于"{query}"的最相关言论：
    
    ### 参考讨论内容 ###
    {context}
    
    ### 任务说明 ###
    请结合上述言论，完成以下深度分析：
    1. 【核心情绪】：现在的股民是处于贪婪恐惧的哪个阶段？
    2. 【观点碰撞】：有哪些尖锐的对立观点？（比如有人看好出海，有人担心关税）
    3. 【技术归纳】：这些帖子中提到了哪些具体的技术信号（如：回踩、量价背离、均线压制等）？
    4. 【操作研判】：结合这些舆情，如果明天出现“低开”，可能是洗盘还是真跌？
    
    注意：请保持客观，区分出“情绪发泄”和“逻辑分析”。
    """
    
    # D. 获取 AI 报告
    report = ask_deepseek_r1(full_prompt)
    print("\n" + "="*50)
    print(f"📊 关于 '{query}' 的舆情技术复盘报告：")
    print("="*50)
    print(report)

if __name__ == "__main__":
    # 你可以尝试输入不同的关键词进行复盘
    smart_review("鲁信")
    # 或者针对你之前的低开迷茫：
    # smart_review("今天低开的原因和明日预期")