# -*- coding: utf-8 -*-

"""
Backbone V2 Engine

功能：

1. 找板块核心股票
2. 计算中军评分
3. 写入 stock_pools

"""


import pandas as pd
import json
import datetime

from sqlalchemy import create_engine, text

from BACKBONE_V2_CONFIG import (
    QUANT_DB_URL,
    REVIEW_DB_URL,
    LOOKBACK_DAYS,
    TOP_N,
    WEIGHTS,
    STATUS,
    POOL_TYPE,
    get_role,
    get_watch_level
)



# ==========================
# 数据库
# ==========================


engine_quant = create_engine(
    QUANT_DB_URL,
    pool_pre_ping=True
)


engine_review = create_engine(
    REVIEW_DB_URL,
    pool_pre_ping=True
)



# ==========================
# 工具函数
# ==========================


def get_latest_date():

    sql = """
    SELECT MAX(trade_date)
    FROM stk_daily_kline
    """

    with engine_quant.connect() as conn:

        return conn.execute(
            text(sql)
        ).scalar()



# ==========================
# 获取板块股票
# ==========================


def load_sector_stocks(
        sector_name,
        trade_date):


    sql=text("""
    SELECT
        r.symbol,
        s.name AS stock_name,
        r.sector_name

    FROM stock_sector_relation r

    JOIN stocks s
    ON r.symbol=s.symbol

    WHERE 
    r.sector_name LIKE :sector

    """)
    

    df=pd.read_sql(
        sql,
        engine_quant,
        params={
            "sector":f"%{sector_name}%"
        }
    )


    return df



# ==========================
# 获取K线
# ==========================


def load_kline(symbols, date):


    sql=text("""
    SELECT
        symbol,
        trade_date,
        close,
        amount,
        volume

    FROM stk_daily_kline

    WHERE symbol in :symbols

    AND trade_date <= :date

    ORDER BY trade_date

    """)


    return pd.read_sql(
        sql,
        engine_quant,
        params={
            "symbols":tuple(symbols),
            "date":date
        }
    )



# ==========================
# 获取资金
# ==========================


def load_capital(symbols,date):


    sql=text("""
    SELECT

    symbol,

    main_net_inflow,
    inflow_3d,
    inflow_5d,
    inflow_10d,

    inflow_days,

    capital_score,
    attack_score

    FROM stk_stock_fund_flow


    WHERE trade_date=:date

    AND symbol in :symbols

    """)


    return pd.read_sql(
        sql,
        engine_quant,
        params={
            "date":date,
            "symbols":tuple(symbols)
        }
    )



# ==========================
# 获取趋势
# ==========================


def load_factor(symbols,date):


    sql=text("""
    SELECT

    symbol,

    f_ma_cohesion,
    f_rsi_14,
    f_mom_20,
    f_macd_hist


    FROM stk_factors


    WHERE trade_date=:date

    AND symbol in :symbols

    """)


    return pd.read_sql(
        sql,
        engine_quant,
        params={
            "date":date,
            "symbols":tuple(symbols)
        }
    )



# ==========================
# 获取板块评分
# ==========================


def load_sector_score(
        sector,
        date):


    sql=text("""
    SELECT *

    FROM stk_sector_scores

    WHERE sector_name=:sector

    AND trade_date=:date

    """)


    df=pd.read_sql(
        sql,
        engine_review,
        params={
            "sector":sector,
            "date":date
        }
    )


    if df.empty:
        return 50


    return float(
        df.iloc[0]
        [
            "total_score"
        ]
    )



# ==========================
# 资金评分
# ==========================


def calc_capital_score(row):


    score=0


    if row.inflow_5d>0:
        score+=8

    if row.inflow_10d>0:
        score+=5

    if row.inflow_days>=3:
        score+=4


    score += (
        min(row.capital_score,100)
        /
        100
        *
        3
    )


    return min(score,20)



# ==========================
# 成交额评分
# ==========================


def calc_amount_score(df):


    avg_amount=(
        df.amount
        .mean()
    )


    max_amount=(
        df.amount
        .max()
    )


    if max_amount==0:
        return 0


    return (
        avg_amount/max_amount
        *
        15
    )


def calc_chip_score(row):

    score = 0


    # 主力控盘
    if row.main_force_control_score >= 80:
        score += 5

    elif row.main_force_control_score >= 60:
        score += 3



    # 资金控盘
    if row.capital_control_score >= 80:
        score += 3

    elif row.capital_control_score >= 60:
        score += 2



    # 筹码质量
    if row.chip_score >= 80:
        score += 3

    elif row.chip_score >= 60:
        score += 2



    # 筹码集中
    if row.chip_width70 <= 15:
        score += 2

    elif row.chip_width70 <= 30:
        score += 1



    # 主力行为
    # 0震荡
    # 1吸筹
    # 2洗盘
    # 3拉升
    # 4出货

    if row.behavior in [1,2,3]:
        score += 2


    # 出货直接扣分

    if row.behavior == 4:
        score -= 5



    return max(score,0)

# ==========================
# 趋势评分
# ==========================


def calc_trend_score(row):


    score=0


    if row.f_rsi_14>=50:
        score+=5


    if row.f_mom_20>0:
        score+=5


    if row.f_macd_hist>0:
        score+=5


    return score


def calc_amount_score(kline_df):


    if kline_df.empty:
        return 0


    scores=[]


    for symbol,df in kline_df.groupby("symbol"):


        df=df.sort_values(
            "trade_date"
        )


        last20=df.tail(20)


        avg_amount=(
            last20.amount.mean()
        )


        latest_amount=(
            last20.iloc[-1].amount
        )


        if avg_amount<=0:
            score=0

        else:

            ratio=(
                latest_amount/
                avg_amount
            )


            if ratio>=1.5:
                score=15

            elif ratio>=1.2:
                score=12

            elif ratio>=0.8:
                score=9

            else:
                score=5


        scores.append(
            {
                "symbol":symbol,
                "amount_score":score
            }
        )


    return pd.DataFrame(scores)

def calc_sync_score(
        symbol,
        sector_symbols,
        kline_df):


    stock=df_stock = (
        kline_df[
            kline_df.symbol==symbol
        ]
        .sort_values("trade_date")
        .tail(20)
    )


    if len(stock)<10:
        return 0



    stock_ret=(
        stock.close
        .pct_change()
    )


    sector=[]


    for s in sector_symbols:


        tmp=(
            kline_df[
                kline_df.symbol==s
            ]
            .sort_values("trade_date")
            .tail(20)
        )


        if len(tmp)==20:

            sector.append(
                tmp.close
                .pct_change()
                .values
            )


    if len(sector)==0:
        return 0



    import numpy as np


    sector_ret=np.mean(
        sector,
        axis=0
    )



    # 同方向天数

    same_direction=(
        (
        stock_ret.values[1:]
        *
        sector_ret[1:]
        )
        >0
    ).sum()



    score=(
        same_direction/
        len(stock_ret[1:])
        *
        15
    )


    return round(score,2)

def calc_leader_bonus(
        stock_name,
        sector_info):


    if (
        stock_name ==
        sector_info["top_stock_name"]
    ):

        return 5


    return 0

def load_chip(symbols,date):


    sql=text("""
    
    SELECT

    symbol,

    chip_score,

    chip_width70,

    main_force_control_score,

    capital_control_score,

    behavior,

    behavior_label,

    cost_profit_pct

    FROM stk_chip_factor


    WHERE trade_date=:date

    AND symbol in :symbols

    """)


    return pd.read_sql(
        sql,
        engine_quant,
        params={
            "date":date,
            "symbols":tuple(symbols)
        }
    )

# ==========================
# 主计算
# ==========================


def calculate_backbone(
        sector,
        date):


    stocks=load_sector_stocks(
        sector,
        date
    )


    if stocks.empty:

        return pd.DataFrame()



    symbols=stocks.symbol.tolist()



    capital=load_capital(
        symbols,
        date
    )


    factors=load_factor(
        symbols,
        date
    )

    chip=load_chip(
        symbols,
        date
    )
    
    kline=load_kline(
        symbols,
        date
    )



    amount_score_df=calc_amount_score(
        kline
    )



    result=stocks.merge(
        capital,
        on="symbol",
        how="left"
    )


    result=result.merge(
        factors,
        on="symbol",
        how="left"
    )


    result=result.merge(
        amount_score_df,
        on="symbol",
        how="left"
    )

    result=result.merge(
        chip,
        on="symbol",
        how="left"
    )

    print(result.dtypes)
    # result.fillna(0,inplace=True)
    print(result.dtypes)

    calc_columns = [
        "capital_score",
        "main_net_inflow",
        "amount_score",
        "f_ma",
        "f_rsi",
        "profit_ratio",
        "chip_score",
        "cost_profit_pct",
        "capital_control_score",
    ]

    for col in calc_columns:
        if col in result.columns:
            result[col] = result[col].fillna(0)

    sector_score = load_sector_score(
        sector,
        date
    )


    sector_score=load_sector_score(
        sector,
        date
    )


    scores=[]


    for _,row in result.iterrows():



        capital_score=(
            calc_capital_score(row)
        )


        trend_score=(
            calc_trend_score(row)
        )


        amount_score=(
            row.amount_score
        )

        chip_score = calc_chip_score(row)


        stock_score=(

            capital_score

            +

            trend_score

            +

            amount_score

        )



        final_score=(

            stock_score

            +

            chip_score
            +

            sector_score*0.2

        )


        scores.append(
            final_score
        )



    result["backbone_score"]=scores



    result["role"]=(
        result.backbone_score
        .apply(get_role)
    )


    return (
        result
        .sort_values(
            "backbone_score",
            ascending=False
        )
        .head(TOP_N)
    )
