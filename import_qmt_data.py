import pandas as pd
import re
from sqlalchemy import create_engine, text

# --- 1. 数据库配置 ---
# 请将 '你的密码' 替换为实际的 MySQL 密码
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def import_sectors_from_file(file_path):
    """
    解析 qmt_概念版块.txt 并提取板块名称存入数据库
    """
    sector_names = []
    
    print(f"正在读取文件: {file_path}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                # 使用正则表达式匹配行格式，例如: "  1. 000809" 或 " 10. 1000SW1国防军工"
                # \d+ 匹配数字编号, \.\s+ 匹配点和空格, (.*) 捕获后面的板块名称
                match = re.search(r'\d+\.\s+(.*)', line)
                if match:
                    sector_name = match.group(1).strip()
                    if sector_name:
                        sector_names.append(sector_name)
        
        if not sector_names:
            print("未能提取到有效的板块名称，请检查文件格式。")
            return

        print(f"成功提取到 {len(sector_names)} 个板块。")

        # 2. 转换为 DataFrame 并去重
        df = pd.DataFrame(sector_names, columns=['name']).drop_duplicates()

        # 3. 写入数据库
        print("正在写入数据库表 'sectors'...")
        with engine.begin() as conn:
            # 批量写入，如果主键冲突则忽略（INSERT IGNORE 的效果）
            for name in df['name']:
                sql = text("INSERT IGNORE INTO sectors (name) VALUES (:name)")
                conn.execute(sql, {"name": name})
        
        print("✅ 板块数据导入完成！")

    except FileNotFoundError:
        print(f"错误：找不到文件 '{file_path}'，请确保文件在脚本同级目录下。")
    except Exception as e:
        print(f"发生错误: {e}")

# --- 运行导入 ---
if __name__ == "__main__":
    # 确保你已经运行了之前的 SQL 建表语句
    import_sectors_from_file('qmt_概念版块.txt')