# -*- coding: utf-8 -*-

"""
RUN_BACKBONE_V2.py

Backbone V2 中军扫描入口

运行:

python RUN_BACKBONE_V2.py 国资云

"""

import sys
import json
import datetime

from sqlalchemy import text

from BACKBONE_V2_ENGINE import (
    calculate_backbone,
    engine_review,
    get_latest_date
)

from BACKBONE_V2_CONFIG import (
    STATUS,
    POOL_TYPE,
    get_watch_level
)



# ==========================
# 写入股票池
# ==========================


def save_stock_pool(
        df,
        sector,
        trade_date):


    if df.empty:

        print("没有结果，不写入")

        return



    sql=text("""
    
    INSERT INTO stock_pools
    (
        symbol,
        trade_date,
        stock_name,

        pool_type,

        sector_name,

        score,

        status,

        tags,

        notes,

        created_at,

        updated_at,

        watch_level,

        prediction_flag,

        prediction_detail,

        viewpoint

    )

    VALUES

    (

        :symbol,

        :trade_date,

        :stock_name,


        :pool_type,


        :sector_name,


        :score,


        :status,


        :tags,


        :notes,


        NOW(),


        NOW(),


        :watch_level,


        99,


        :prediction_detail,


        :viewpoint

    )


    ON DUPLICATE KEY UPDATE


    score=VALUES(score),

    tags=VALUES(tags),

    notes=VALUES(notes),

    updated_at=NOW(),

    watch_level=VALUES(watch_level),

    viewpoint=VALUES(viewpoint)

    """)



    with engine_review.begin() as conn:


        for _,row in df.iterrows():


            score=round(
                float(row.backbone_score),
                2
            )


            role=row.role



            tags={

                "strategy":
                    "BACKBONE_V2",


                "role":
                    role,


                "backbone_score":
                    score,


                "sector":
                    sector

            }



            notes=(

                f"{sector}板块{role};"

                f"中军评分{score};"

                "资金趋势+成交额+趋势综合判断"

            )



            viewpoint=(

                f"{sector}主线观察股票，"

                f"当前定位:{role}。"

                "关注板块持续性和资金变化。"

            )



            conn.execute(
                sql,
                {

                "symbol":
                    row.symbol,


                "trade_date":
                    trade_date,


                "stock_name":
                    row.stock_name,


                "pool_type":
                    POOL_TYPE,


                "sector_name":
                    sector,


                "score":
                    score,


                "status":
                    STATUS,


                "tags":
                    json.dumps(
                        tags,
                        ensure_ascii=False
                    ),


                "notes":
                    notes,


                "watch_level":
                    get_watch_level(
                        score
                    ),


                "prediction_detail":
                    role,


                "viewpoint":
                    viewpoint

                }
            )


    print(
        "\n✅ 已写入 stock_pools"
    )



# ==========================
# 输出结果
# ==========================


def print_result(
        df,
        sector,
        date):


    print("\n")
    print("="*70)

    print(
        f"🔥 Backbone V2 板块中军扫描"
    )

    print(
        f"板块:{sector}"
    )

    print(
        f"交易日期:{date}"
    )


    print("="*70)



    print(
        f"{'排名':<6}"
        f"{'股票':<12}"
        f"{'评分':<10}"
        f"{'角色':<10}"
    )



    for i,row in enumerate(
        df.itertuples(),
        1
    ):


        print(

            f"{i:<6}"

            f"{row.stock_name:<12}"

            f"{row.backbone_score:<10.2f}"

            f"{row.role:<10}"

        )



    print("="*70)





# ==========================
# main
# ==========================


def main():


    if len(sys.argv)<2:


        print(
            """
请输入板块名称:

例如:

python RUN_BACKBONE_V2.py 国资云

"""
        )

        return



    sector=sys.argv[1]



    date=get_latest_date()



    print(
        f"""
启动 Backbone V2

板块:
{sector}

日期:
{date}

"""
    )



    df=calculate_backbone(
        sector,
        date
    )



    if df.empty:


        print(
            "❌ 没有找到股票"
        )

        return



    print_result(
        df,
        sector,
        date
    )



    # save_stock_pool(
    #     df,
    #     sector,
    #     date
    # )




if __name__=="__main__":

    main()