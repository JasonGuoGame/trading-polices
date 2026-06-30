import os
import requests
import json
import chromadb
from sqlalchemy import create_engine, text

# 🌟 核心修正：在所有代码运行前，彻底抹除系统代理环境变量
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 初始化 Chroma 和 MySQL
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')
client = chromadb.PersistentClient(path="./v_db_shuimu")
collection = client.get_or_create_collection(name="market_sentiment")

def get_embedding_safe(text_input):
    url = "http://127.0.0.1:11434/api/embeddings"
    payload = {
        "model": "bge-m3:latest",
        "prompt": text_input[:800] # 进一步缩短长度确保稳定
    }
    
    # 🌟 核心修正：设置 proxies={'http': None, 'https': None} 强制直连
    try:
        response = requests.post(
            url, 
            json=payload, 
            timeout=30, 
            proxies={'http': None, 'https': None}
        )
        if response.status_code == 200:
            return response.json()['embedding']
        else:
            print(f"Ollama Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"连接失败: {e}")
        return None

def main():
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, title, content FROM forum_post WHERE embedding_done = 0 LIMIT 50")).fetchall()
        if not rows:
            print("☕ 暂无新帖子需要处理")
            return

        for row in rows:
            print(f"正在处理 ID {row.id}: {row.title[:15]}...")
            vec = get_embedding_safe(f"{row.title}\n{row.content}")
            
            if vec:
                # 存入 Chroma
                collection.add(
                    ids=[str(row.id)],
                    embeddings=[vec],
                    documents=[f"{row.title}\n{row.content}"[:1000]],
                    metadatas=[{"title": row.title}]
                )
                # 更新 MySQL
                conn.execute(text("UPDATE forum_post SET embedding_done = 1 WHERE id = :id"), {"id": row.id})
                conn.commit()
                print(f"✅ ID {row.id} 成功")
            else:
                print(f"❌ ID {row.id} 失败")

if __name__ == "__main__":
    main()