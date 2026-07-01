import os
import requests
import json
import chromadb
from sqlalchemy import create_engine, text

# 彻底屏蔽系统代理
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 初始化
engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')
client = chromadb.PersistentClient(path="./v_db_shuimu")
collection = client.get_or_create_collection(name="market_sentiment")

def get_embedding_safe(text_input):
    url = "http://127.0.0.1:11434/api/embeddings"
    payload = {"model": "bge-m3:latest", "prompt": text_input[:800]}
    try:
        response = requests.post(url, json=payload, timeout=30, proxies={'http': None, 'https': None})
        if response.status_code == 200:
            return response.json()['embedding']
        return None
    except Exception as e:
        print(f"连接失败: {e}")
        return None

def main():
    with engine.connect() as conn:
        # 🌟 修复 1：在 SQL 中增加 author 字段
        rows = conn.execute(text("SELECT id, title, content, author FROM forum_post WHERE embedding_done = 0 LIMIT 50")).fetchall()
        
        if not rows:
            print("☕ 暂无新帖子需要处理")
            return

        for row in rows:
            print(f"正在处理 ID {row.id}: {row.title[:15]}...")
            full_content = f"{row.title}\n{row.content}"
            vec = get_embedding_safe(full_content)
            
            if vec:
                # 🌟 修复 2：在 metadatas 中增加 author 字段
                # 提示：Chroma 要求 metadata 的值不能为空，所以加个 or "未知"
                collection.add(
                    ids=[str(row.id)],
                    embeddings=[vec],
                    documents=[full_content[:1000]],
                    metadatas=[{
                        "title": row.title,
                        "author": str(row.author) if row.author else "匿名用户"
                    }]
                )
                # 更新 MySQL
                conn.execute(text("UPDATE forum_post SET embedding_done = 1 WHERE id = :id"), {"id": row.id})
                conn.commit()
                print(f"✅ ID {row.id} 成功")
            else:
                print(f"❌ ID {row.id} 失败")

if __name__ == "__main__":
    main()