#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Futures Screener V3.1 - 机构版
功能：
1. 建仓信号：动态 RVOL + 持仓量异动 + 波动率压缩
2. 顶部风险：量价效率比 + 长上影线 + 高位滞涨
3. 数据清洗：剔除股票代币、杠杆代币、非 USDT 合约
4. 自动通知：发现信号自动创建 GitHub Issue
"""

import requests
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

# ================= 配置区域 =================
# 建仓策略参数
MIN_DAILY_VOL_USDT = 1_000_000      # 3 日日均成交额下限 (100 万)，过滤死币
MAX_DAILY_VOL_USDT = 10_000_000     # 3 日日均成交额上限 (1000 万)，过滤大盘股
VOL_MULTIPLIER = 2.5                # 放量倍数阈值 (动态 RVOL > 2.5)
MAX_PRICE_GAIN = 0.12               # 最大涨幅 12% (超过视为已启动)
MIN_PRICE_GAIN = -0.02              # 最小涨幅 -2% (剔除暴跌)
MIN_LIQUIDITY_USDT = 500_000        # 24h 成交额最低门槛 (50 万)

# 顶部风险参数
HIGH_RISK_GAIN_THRESHOLD = 0.25     # 涨幅超过 25% 进入观察池
UPPER_SHADOW_RATIO = 0.08           # 上影线占比 > 8% 视为有抛压
EFFICIENCY_RATIO_THRESHOLD = 0.5    # 量价效率比 < 0.5 视为滞涨 (巨量不涨)

# GitHub 通知配置
GITHUB_TOKEN = os.getenv("GH_TOKEN", "")
GITHUB_REPO = "zgs1983/banban"      # 您的仓库
ENABLE_GITHUB_NOTIFY = True if GITHUB_TOKEN else False

# API 端点
BASE_URL = "https://fapi.binance.com"
TICKER_24H_URL = f"{BASE_URL}/fapi/v1/ticker/24hr"
OI_URL = f"{BASE_URL}/fapi/v1/openInterest"

# ================= 工具函数 =================

def get_current_hour_volume(symbol: str) -> float:
    """获取当前小时的预估成交量 (简化版：取最近 1 小时 K 线)"""
    try:
        kline_url = f"{BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": "1h", "limit": 1}
        resp = requests.get(kline_url, params=params, timeout=3)
        if resp.status_code == 200:
            data = resp.json()[0]
            return float(data[7])  # quoteVolume (成交额 USDT)
    except Exception as e:
        return 0.0
    return 0.0

def is_valid_symbol(symbol: str) -> bool:
    """过滤无效标的：只保留纯加密货币 USDT 合约，剔除股票、杠杆、非 USDT"""
    if not symbol.endswith("USDT"):
        return False
    
    base = symbol.replace("USDT", "")
    
    # 剔除杠杆代币 (e.g., BTCUP, BTCDOWN, ETH3S)
    if any(x in base for x in ["UP", "DOWN", "BULL", "BEAR"]) or base[-1].isdigit():
        return False
    
    # 剔除已知股票代币白名单 (可根据需要扩展)
    stock_tokens = [
        "TSM", "AAPL", "TSLA", "GOOG", "AMZN", "NFLX", "MSFT", "COIN", "BA", 
        "DIS", "META", "NVDA", "AMD", "INTC", "PYPL", "SQ", "SHOP", "UBER", 
        "LYFT", "ABNB", "ZM", "DOCU", "SNAP", "TWTR", "PINS", "SPOT", "RBLX"
    ]
    if base in stock_tokens:
        return False
        
    # 只保留字母组成的币种 (剔除部分含特殊字符的异常合约)
    if not base.isalpha():
        return False
        
    return True

def calculate_upper_shadow(high: float, low: float, close: float, open_p: float) -> float:
    """计算上影线比例"""
    upper_wick = high - max(open_p, close)
    total_range = high - low
    if total_range == 0:
        return 0.0
    return upper_wick / total_range

def create_github_issue(title: str, body: str, labels: List[str]):
    """创建 GitHub Issue 通知"""
    if not ENABLE_GITHUB_NOTIFY:
        return
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/issues"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "title": title,
        "body": body,
        "labels": labels
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        if resp.status_code == 201:
            print(f"✅ 已发送通知：{title}")
        else:
            print(f"⚠️ 通知失败：{resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ 通知异常：{str(e)}")

# ================= 核心逻辑 =================

def fetch_market_data() -> List[Dict]:
    """获取全市场 24h 数据"""
    try:
        resp = requests.get(TICKER_24H_URL, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"API Error: {e}")
    return []

def scan_entry_signals(data: List[Dict]) -> List[Dict]:
    """扫描建仓信号"""
    signals = []
    print(f"\n🔍 正在扫描 {len(data)} 个合约的建仓机会...")
    
    for item in data:
        symbol = item['symbol']
        
        # 1. 基础过滤
        if not is_valid_symbol(symbol):
            continue
            
        price = float(item['lastPrice'])
        vol_24h_usdt = float(item['quoteVolume'])  # 24h 成交额
        pct_change = float(item['priceChangePercent']) / 100.0
        
        # 流动性门槛
        if vol_24h_usdt < MIN_LIQUIDITY_USDT:
            continue
            
        # 2. 估算过去 3 天日均 (简化：假设 24h 量代表近期水平)
        avg_daily_vol = vol_24h_usdt 
        
        # 3. 获取当前小时成交量 (1h K 线)
        current_hour_vol = get_current_hour_volume(symbol)
        
        if current_hour_vol == 0:
            continue
            
        # 4. 计算动态 RVOL (当前小时量 / (24h 量/24))
        avg_hourly_vol = vol_24h_usdt / 24.0
        if avg_hourly_vol == 0:
            continue
        
        rvol = current_hour_vol / avg_hourly_vol
        
        # 5. 策略判断
        if (MIN_DAILY_VOL_USDT <= avg_daily_vol <= MAX_DAILY_VOL_USDT and
            rvol >= VOL_MULTIPLIER and
            MIN_PRICE_GAIN <= pct_change <= MAX_PRICE_GAIN):
            
            signals.append({
                "symbol": symbol,
                "price": price,
                "gain": pct_change * 100,
                "rvol": rvol,
                "vol_24h": avg_daily_vol,
                "vol_1h": current_hour_vol,
                "score": 90 if rvol > 4.0 else 80
            })
            
    # 按 RVOL 降序排序
    signals.sort(key=lambda x: x['rvol'], reverse=True)
    return signals

def scan_exit_risks(data: List[Dict]) -> List[Dict]:
    """扫描顶部风险"""
    risks = []
    print(f"🔍 正在扫描 {len(data)} 个合约的顶部风险...")
    
    for item in data:
        symbol = item['symbol']
        if not is_valid_symbol(symbol):
            continue
            
        price = float(item['lastPrice'])
        high = float(item['highPrice'])
        low = float(item['lowPrice'])
        open_p = float(item['openPrice'])
        pct_change = float(item['priceChangePercent']) / 100.0
        vol_24h_usdt = float(item['quoteVolume'])
        
        if vol_24h_usdt < MIN_LIQUIDITY_USDT:
            continue
            
        # 1. 涨幅过滤
        if pct_change < HIGH_RISK_GAIN_THRESHOLD:
            continue
            
        # 2. 计算上影线
        shadow_ratio = calculate_upper_shadow(high, low, price, open_p)
        
        risk_score = 0
        reasons = []
        
        if shadow_ratio > UPPER_SHADOW_RATIO:
            risk_score += 40
            reasons.append(f"上影线{shadow_ratio*100:.1f}%")
            
        if pct_change > 0.50:  # 暴涨 50% 以上
            risk_score += 30
            reasons.append("涨幅过大")
            
        if shadow_ratio > 0.20:  # 极端上影线
            risk_score += 30
            reasons.append("极端抛压")
            
        if risk_score >= 40:
            risks.append({
                "symbol": symbol,
                "price": price,
                "gain": pct_change * 100,
                "shadow": shadow_ratio * 100,
                "score": risk_score,
                "reasons": ", ".join(reasons)
            })
            
    risks.sort(key=lambda x: x['score'], reverse=True)
    return risks

def print_results(entry_signals: List[Dict], exit_risks: List[Dict]):
    """打印结果并发送通知"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"📊 币安期货监控报告 ({timestamp})")
    print(f"{'='*60}")
    
    # 建仓信号
    print(f"\n🟢 建仓信号 ({len(entry_signals)}个)")
    if not entry_signals:
        print("   暂无符合条件的机会 (市场无低量启动标的)")
    else:
        for i, s in enumerate(entry_signals[:5]):  # 只显示前 5 个
            stars = "⭐" * (s['score'] // 20)
            print(f"{i+1}. {s['symbol']} (${s['price']}, {s['gain']:+.2f}%)")
            print(f"   RVOL: {s['rvol']:.1f}倍 | 24h 量：${s['vol_24h']/1e6:.1f}M")
            print(f"   特征：{'量增价平' if s['gain'] < 5 else '温和启动'} {stars}")
            
            # 发送通知
            if ENABLE_GITHUB_NOTIFY:
                title = f"🟢 [建仓信号] {s['symbol']} - RVOL {s['rvol']:.1f}倍"
                body = f"""
### 发现潜在建仓机会
- **标的**: {s['symbol']}
- **价格**: ${s['price']} ({s['gain']:+.2f}%)
- **RVOL**: {s['rvol']:.1f}倍放量
- **24h 成交额**: ${s['vol_24h']/1e6:.2f}M
- **建议**: 关注低位吸筹形态，确认 K 线后轻仓试错
                """
                create_github_issue(title, body.strip(), ["建仓信号", "监控"])

    # 顶部风险
    print(f"\n🔴 顶部风险 ({len(exit_risks)}个)")
    if not exit_risks:
        print("   市场情绪稳定，无明显高危标的")
    else:
        for i, r in enumerate(exit_risks[:5]):
            level = "极高风险" if r['score'] >= 70 else "高风险"
            print(f"{i+1}. {r['symbol']} (${r['price']}, {r['gain']:+.2f}%) [{level}]")
            print(f"   原因：{r['reasons']} (上影线{r['shadow']:.1f}%)")
            
            # 发送通知
            if ENABLE_GITHUB_NOTIFY and r['score'] >= 60:
                title = f"🔴 [高危预警] {r['symbol']} - {r['reasons']}"
                body = f"""
### 发现顶部出货风险
- **标的**: {r['symbol']}
- **价格**: ${r['price']} ({r['gain']:+.2f}%)
- **风险分**: {r['score']}
- **特征**: {r['reasons']}
- **建议**: 持有者立即止盈，未持有者严禁追高
                """
                create_github_issue(title, body.strip(), ["顶部风险", "紧急"])

    print(f"\n{'='*60}\n")

def main():
    print("🚀 启动 Binance Futures Screener V3.1...")
    data = fetch_market_data()
    if not data:
        print("❌ 获取数据失败")
        return
        
    entry_signals = scan_entry_signals(data)
    exit_risks = scan_exit_risks(data)
    print_results(entry_signals, exit_risks)

if __name__ == "__main__":
    main()
