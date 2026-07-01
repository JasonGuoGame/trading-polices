import chromadb
import os

# 1. 检查文件夹是否存在
db_path = "./v_db_shuimu"
if os.path.exists(db_path):
    print(f"✅ 找到数据库文件夹: {os.path.abspath(db_path)}")
else:
    print(f"❌ 未找到数据库文件夹: {db_path}")

try:
    # 2. 尝试连接（本地模式）
    client = chromadb.PersistentClient(path=db_path)
    
    # 3. 检查心跳（判断库是否正常加载）
    heartbeat = client.heartbeat()
    print(f"💓 数据库心跳正常: {heartbeat}")

    # 4. 列出所有集合（Collection）
    collections = client.list_collections()
    print(f"📚 当前拥有的集合: {[c.name for c in collections]}")

    # 5. 检查特定集合内的数据量
    if collections:
        collection = client.get_collection(name="market_sentiment")
        count = collection.count()
        print(f"📊 'market_sentiment' 集合内共有 {count} 条向量数据")
        
        # 打印最后一条存入的数据预览
        if count > 0:
            last_item = collection.get(limit=1)
            print(f"🔍 数据预览: {last_item['documents'][0][:50]}...")

except Exception as e:
    print(f"💥 数据库连接失败: {e}")