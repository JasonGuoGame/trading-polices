import requests
from bs4 import BeautifulSoup
import time
import datetime
from sqlalchemy import create_engine, text
import os
import random

# 屏蔽代理干扰
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# =================================================
# 🌟 填入你浏览器里的完整 Cookie
# =================================================
MY_COOKIE = "Hm_lvt_bbac0322e6ee13093f98d5c4b5a10912=1782782154,1782822521; HMACCOUNT=BF1EAB14792B6911; __gads=ID=3b056e749e8f0732:T=1782782181:RT=1782867852:S=ALNI_Ma5xvyDhOkSTlEPUQBLq3OazTLxYQ; __gpi=UID=0000140519f0fcf6:T=1782782181:RT=1782867852:S=ALNI_Ma8O7ylQf__9lGy6xmgxT75sAO2ng; __eoi=ID=b8b049934c9d968b:T=1782782181:RT=1782867852:S=AA-AfjYiO1Hi_IO_vOouykpm4hSu; main[UTMPUSERID]=gcgame; main[UTMPKEY]=56865029; main[UTMPNUM]=2676; Hm_lpvt_bbac0322e6ee13093f98d5c4b5a10912=1782867894"

engine = create_engine('mysql+pymysql://root:root_secret_2026@localhost:3306/trading_review')

class ShuimuIncrementalCrawler:
    def __init__(self, cookie):
        self.base_url = "https://m.newsmth.net"
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Cookie": cookie,
            "Referer": "https://m.newsmth.net/",
        }
        self.existing_titles = self._get_existing_titles()

    def _get_existing_titles(self):
        """预先从数据库读取最近 500 条标题，用于快速比对"""
        try:
            with engine.connect() as conn:
                query = text("SELECT title FROM forum_post ORDER BY id DESC LIMIT 500")
                res = conn.execute(query).fetchall()
                return set([r[0] for r in res])
        except Exception as e:
            print(f"读取旧数据失败: {e}")
            return set()

    def fetch_post_content(self, article_url):
        """🌟 核心新增：进入帖子详情页抓取正文"""
        try:
            resp = self.session.get(article_url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                # 移动版正文通常在 menu 之后的文本中
                # 我们抓取整个页面的 text 并进行简单清洗
                raw_text = soup.get_text(separator=' ', strip=True)
                
                # 清洗逻辑：水木帖子通常以“发信站:”开头
                if "发信站:" in raw_text:
                    content = raw_text.split("发信站:")[-1]
                else:
                    content = raw_text
                
                # 截取前 3000 字，防止单个帖子过长
                return content[:3000]
            return ""
        except Exception as e:
            print(f"      ❌ 抓取正文失败: {article_url} -> {e}")
            return ""

    def run(self, max_pages=20):
        print(f"[{datetime.datetime.now()}] 🌙 启动夜间增量同步（正文抓取模式）...")
        
        new_posts_total = 0
        duplicate_count = 0
        stop_threshold = 8

        for p in range(1, max_pages + 1):
            url = f"https://m.newsmth.net/board/Stock?p={p}"
            print(f"正在扫描第 {p} 页...")
            
            try:
                resp = self.session.get(url, headers=self.headers, timeout=30)
                if "请登录后继续" in resp.text:
                    print("❌ Cookie 过期！请更新。")
                    return

                soup = BeautifulSoup(resp.text, 'html.parser')
                items = soup.find_all('li')
                
                page_posts = []
                for li in items:
                    title_tag = li.find('a', href=True)
                    if not title_tag or '/article/Stock/' not in title_tag['href']:
                        continue
                        
                    title = title_tag.get_text(strip=True)
                    
                    if title in self.existing_titles:
                        duplicate_count += 1
                        if duplicate_count >= stop_threshold:
                            print(f"🛑 检测到连续 {duplicate_count} 个重复，增量同步完成。")
                            self.save_to_db(page_posts)
                            print(f"✨ 本次共新增 {new_posts_total} 个帖子。")
                            return
                        continue
                    
                    duplicate_count = 0
                    
                    # 提取作者
                    author_tag = li.find('a', href=lambda x: x and '/user/query/' in x)
                    author = author_tag.get_text(strip=True) if author_tag else "未知用户"
                    
                    # 🌟 核心修改：获取帖子详情链接并抓取正文
                    article_href = title_tag['href']
                    article_url = self.base_url + article_href
                    
                    print(f"   -> 正在读取正文: {title[:15]}...")
                    # 调用新加的抓取正文函数
                    content = self.fetch_post_content(article_url)
                    
                    if "版规" in title or "置顶" in title:
                        continue

                    page_posts.append({
                        'title': title, 
                        'author': author, 
                        'content': content # 现在这里有内容了
                    })
                    new_posts_total += 1
                    
                    # 抓取详情页之间也要稍作休息，保护 Cookie
                    time.sleep(random.uniform(0.5, 1.5))

                if page_posts:
                    self.save_to_db(page_posts)
                
                time.sleep(random.uniform(2, 4))

            except Exception as e:
                print(f"💥 第 {p} 页出错: {e}")
                break

    def save_to_db(self, posts):
        if not posts: return
        success_count = 0
        with engine.begin() as conn:
            for post in posts:
                try:
                    # 🌟 修改：SQL 绑定参数中使用 :c 对应 content
                    conn.execute(text("""
                        INSERT IGNORE INTO forum_post (source, author, title, content, created_time) 
                        VALUES ('水木论坛', :a, :t, :c, :now)
                    """), {
                        "a": post['author'], 
                        "t": post['title'], 
                        "c": post['content'], # 传入抓取到的正文
                        "now": datetime.datetime.now()
                    })
                    success_count += 1
                except Exception as e:
                    print(f"插入数据库失败: {e}")
                    continue

if __name__ == "__main__":
    crawler = ShuimuIncrementalCrawler(MY_COOKIE)
    crawler.run()