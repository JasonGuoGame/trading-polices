import pandas as pd
from sqlalchemy import create_engine, text
import re

# --- 1. 数据库配置 ---
DB_URL = 'mysql+pymysql://root:root_secret_2026@localhost:3306/quant_db'
engine = create_engine(DB_URL)

def is_hu_shen_a_share(symbol, name):
    """
    判断是否为真正的沪深 A 股
    规则：代码必须是 60, 00, 30, 688 开头，且不能包含“指数”字样
    """
    # 1. 排除指数
    if "指数" in name or "板块" in name:
        return False
    
    # 2. 拆分代码
    match = re.match(r'^(\d{6})\.(SH|SZ)$', symbol)
    if not match:
        return False
    
    code = match.group(1)
    # 3. 匹配 A 股号段
    # 60:沪主板, 00:深主板/中小板, 30:创业板, 688:科创板
    if code.startswith(('60', '00', '30', '688')):
        return True
    
    return False

def import_only_a_shares(file_path):
    print(f"正在深度过滤并解析: {file_path}")
    
    valid_stocks = []      # 存储 (symbol, name)
    valid_relations = []   # 存储 (symbol, sector_name)
    used_sectors = set()   # 存储在 A 股中真正出现的板块

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[2:] # 跳过表头
            
            for line in lines:
                line = line.strip()
                if not line or '\t' not in line: continue
                
                parts = line.split('\t')
                if len(parts) < 3: continue
                
                symbol = parts[0].strip()
                name = parts[1].strip()
                industry_str = parts[2].strip()
                
                # --- 核心过滤步骤 ---
                if is_hu_shen_a_share(symbol, name):
                    valid_stocks.append({'symbol': symbol, 'name': name})
                    
                    # 拆分行业并清理名称
                    sectors = [s.strip() for s in industry_str.split(',')]
                    for s in sectors:
                        if s:
                            used_sectors.add(s)
                            valid_relations.append({'symbol': symbol, 'sector_name': s})

        # --- 2. 转换为 DataFrame ---
        df_stocks = pd.DataFrame(valid_stocks).drop_duplicates(subset=['symbol'])
        df_relations = pd.DataFrame(valid_relations).drop_duplicates()
        df_sectors = pd.DataFrame(list(used_sectors), columns=['name'])

        print(f"过滤完成：共保留 {len(df_stocks)} 只沪深 A 股，涉及 {len(df_sectors)} 个板块。")

        # --- 3. 写入数据库 ---
        with engine.begin() as conn:
            # 清理旧数据，保证“纯净”
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            conn.execute(text("TRUNCATE TABLE stock_sector_relation;"))
            conn.execute(text("TRUNCATE TABLE stocks;"))
            conn.execute(text("TRUNCATE TABLE sectors;"))

            print("写入板块字典...")
            df_sectors.to_sql('sectors', con=conn, if_exists='append', index=False, method='multi')
            
            print("写入股票基本信息...")
            df_stocks.to_sql('stocks', con=conn, if_exists='append', index=False, method='multi')
            
            print("写入 A 股-板块对应关系...")
            df_relations.to_sql('stock_sector_relation', con=conn, if_exists='append', index=False, method='multi')
            
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))

        print("✅ 沪深 A 股数据精准同步完成！")

    except Exception as e:
        print(f"❌ 导入失败: {e}")

if __name__ == "__main__":
    import_only_a_shares('all_stocks_industry_info.txt')