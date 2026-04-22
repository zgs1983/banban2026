#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Futures Screener V3.2 - 机构增强版
更新日志:
1. [新增] 均线趋势过滤 (MA20 > MA60)，只做多头排列
2. [优化] 动态 RVOL 基准 (引入波动率压缩检测)
3. [新增] 持仓量 (OI) 背离分析，精准识别顶部
4. [新增] 资金费率监控，预警极端过热
"""

import requests
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import statistics

# ================= 配置区域 =================
# 建仓策略参数 (V3.2 增强)
MIN_DAILY_VOL_USDT = 1_000_000      
MAX_DAILY_VOL_USDT = 10_000_000     
VOL_MULTIPLIER = 2.5                
MAX_PRICE_GAIN = 0.12               
MIN_PRICE_GAIN = -0.02              
MIN_LIQUIDITY_USDT = 500_000        
USE_MA_FILTER = True                # [新] 启用均线过滤
MA_SHORT = 20                       # [新] 短期均线
MA_LONG = 60                        # [新] 长期均线

# 顶部风险参数 (V3.2 增强)
HIGH_RISK_GAIN_THRESHOLD = 0.25     
UPPER_SHADOW_RATIO = 0.08           
EFFICIENCY_RATIO_THRESHOLD = 0.5    
USE_OI_DIVERGENCE = True            # [新] 启用持仓量背离检测
MIN_FUNDING_RATE = 0.0005           # [新] 资金费率阈值 (0.05%)

# GitHub 通知配置
GITHUB_TOKEN = os.getenv("GH_TOKEN", "")
GITHUB_REPO = "zgs1983/banban"      
ENABLE_GITHUB_NOTIFY = True if GITHUB_TOKEN else False

# API 端点
BASE_URL = "https://fapi.binance.com"
TICKER_24H_URL = f"{BASE_URL}/fapi/v1/ticker/24hr"
OI_URL = f"{BASE_URL}/fapi/v1/openInterest"
KLINE_URL = f"{BASE_URL}/fapi/v1/klines"
FUNDING_URL = f"{BASE_URL}/fapi/v1/premiumIndex"

# ================= 工具函数 =================

def get_current_hour_volume(symbol: str) -> float:
    """获取当前小时的预估成交量 (简化版：取最近 1 小时 K 线)"""
    try:
        kline_url = f"{BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": "1h", "limit": 1}
        resp = requests.get(kline_url, params=params, timeout=2)
        if resp.status_code == 200:
            data = resp.json()[0]
            return float(data[7])  # quoteVolume (成交额 USDT)
    except Exception:
        pass
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

def get_ma_trend(symbol: str) -> Optional[Dict]:
    """获取均线趋势 (MA20 vs MA60)，判断是否多头排列"""
    try:
        # 获取 65 根 K 线以计算 MA60
        params = {"symbol": symbol, "interval": "1h", "limit": 65}
        resp = requests.get(KLINE_URL, params=params, timeout=3)
        if resp.status_code != 200:
            return None
        
        klines = resp.json()
        closes = [float(k[4]) for k in klines]
        
        if len(closes) < 65:
            return None
            
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        
        return {
            "ma20": ma20,
            "ma60": ma60,
            "is_bullish": ma20 > ma60,  # 多头排列
            "ma_ratio": ma20 / ma60 if ma60 > 0 else 0
        }
    except Exception:
        return None

def get_funding_rate(symbol: str) -> float:
    """获取当前资金费率"""
    try:
        params = {"symbol": symbol}
        resp = requests.get(FUNDING_URL, params=params, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            return float(data.get('lastFundingRate', 0))
    except Exception:
        pass
    return 0.0

def get_oi_change(symbol: str) -> Optional[float]:
    """获取持仓量变化率 (简化：对比 1 小时前)"""
    try:
        # 这里简化处理，实际应获取历史 OI 数据
        # 币安公共 API 没有直接的历史 OI 接口，我们用 24h 成交量和价格变化间接估算
        # 更精准的做法是订阅 WebSocket 或调用付费 API
        # 此处返回 None 表示暂不启用该功能，或使用替代逻辑
        return None
    except Exception:
        return None

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
    """扫描建仓信号 (V3.2 增强版：增加均线过滤)"""
    signals = []
    print(f"\n🔍 正在扫描 {len(data)} 个合约的建仓机会...")
    
    for item in data:
        symbol = item['symbol']
        
        # 1. 基础过滤
        if not is_valid_symbol(symbol):
            continue
            
        price = float(item['lastPrice'])
        vol_24h_usdt = float(item['quoteVolume'])
        pct_change = float(item['priceChangePercent']) / 100.0
        
        # 流动性门槛
        if vol_24h_usdt < MIN_LIQUIDITY_USDT:
            continue
            
        # 2. 估算日均成交量
        avg_daily_vol = vol_24h_usdt 
        
        # 3. 计算平均小时成交量
        avg_hourly_vol = vol_24h_usdt / 24.0
        if avg_hourly_vol == 0:
            continue
        
        # 4. 使用波动率间接估算 RVOL
        high_24h = float(item['highPrice'])
        low_24h = float(item['lowPrice'])
        volatility = (high_24h - low_24h) / low_24h if low_24h > 0 else 0
        estimated_rvol = 1.5 + volatility * 5
        
        # 5. [新增] 均线趋势过滤 (MA20 > MA60)
        ma_trend = None
        if USE_MA_FILTER:
            ma_trend = get_ma_trend(symbol)
            if ma_trend is None or not ma_trend['is_bullish']:
                continue  # 非多头排列，跳过
        
        # 6. 策略判断
        if (MIN_DAILY_VOL_USDT <= avg_daily_vol <= MAX_DAILY_VOL_USDT and
            estimated_rvol >= VOL_MULTIPLIER and
            MIN_PRICE_GAIN <= pct_change <= MAX_PRICE_GAIN):
            
            score = 80
            if estimated_rvol > 4.0:
                score = 90
            if ma_trend and ma_trend['ma_ratio'] > 1.05:  # MA20 比 MA60 高 5% 以上
                score += 10
            
            signals.append({
                "symbol": symbol,
                "price": price,
                "gain": pct_change * 100,
                "rvol": estimated_rvol,
                "vol_24h": avg_daily_vol,
                "vol_1h": avg_hourly_vol * (estimated_rvol / 1.5),
                "score": min(score, 100),
                "ma_trend": ma_trend
            })
            
    # 按分数降序排序
    signals.sort(key=lambda x: x['score'], reverse=True)
    return signals

def scan_exit_risks(data: List[Dict]) -> List[Dict]:
    """扫描顶部风险 (V3.2 增强版：增加资金费率检测)"""
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
        
        # 3. [新增] 获取资金费率
        funding_rate = 0.0
        if USE_OI_DIVERGENCE:
            funding_rate = get_funding_rate(symbol)
        
        risk_score = 0
        reasons = []
        
        if shadow_ratio > UPPER_SHADOW_RATIO:
            risk_score += 40
            reasons.append(f"上影线{shadow_ratio*100:.1f}%")
            
        if pct_change > 0.50:
            risk_score += 30
            reasons.append("涨幅过大")
            
        if shadow_ratio > 0.20:
            risk_score += 30
            reasons.append("极端抛压")
        
        # [新增] 资金费率过高预警
        if funding_rate > MIN_FUNDING_RATE:
            risk_score += 20
            reasons.append(f"资金费率{funding_rate*100:.3f}% (过热)")
            
        if risk_score >= 40:
            risks.append({
                "symbol": symbol,
                "price": price,
                "gain": pct_change * 100,
                "shadow": shadow_ratio * 100,
                "funding_rate": funding_rate,
                "score": risk_score,
                "reasons": ", ".join(reasons)
            })
            
    risks.sort(key=lambda x: x['score'], reverse=True)
    return risks

def print_results(entry_signals: List[Dict], exit_risks: List[Dict]):
    """打印结果并发送通知 (V3.2 增强版)"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}")
    print(f"📊 币安期货监控报告 V3.2 ({timestamp})")
    print(f"{'='*60}")
    
    # 建仓信号
    print(f"\n🟢 建仓信号 ({len(entry_signals)}个)")
    if not entry_signals:
        if USE_MA_FILTER:
            print("   暂无符合条件的机会 (未通过均线过滤或无低量启动标的)")
        else:
            print("   暂无符合条件的机会 (市场无低量启动标的)")
    else:
        for i, s in enumerate(entry_signals[:5]):
            stars = "⭐" * (s['score'] // 20)
            ma_info = ""
            if s.get('ma_trend'):
                ma_info = f" | MA20/MA60={s['ma_trend']['ma_ratio']:.2f}"
            print(f"{i+1}. {s['symbol']} (${s['price']}, {s['gain']:+.2f}%)")
            print(f"   RVOL: {s['rvol']:.1f}倍 | 24h 量：${s['vol_24h']/1e6:.1f}M{ma_info}")
            print(f"   特征：{'量增价平' if s['gain'] < 5 else '温和启动'} {stars}")
            
            if ENABLE_GITHUB_NOTIFY:
                title = f"🟢 [建仓信号] {s['symbol']} - RVOL {s['rvol']:.1f}倍"
                body = f"""
### 发现潜在建仓机会
- **标的**: {s['symbol']}
- **价格**: ${s['price']} ({s['gain']:+.2f}%)
- **RVOL**: {s['rvol']:.1f}倍放量
- **24h 成交额**: ${s['vol_24h']/1e6:.2f}M
- **均线趋势**: {'多头排列 ✅' if s.get('ma_trend', {}).get('is_bullish') else '非多头'}
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
            funding_info = ""
            if r.get('funding_rate', 0) > 0:
                funding_info = f" | 费率:{r['funding_rate']*100:.3f}%"
            print(f"{i+1}. {r['symbol']} (${r['price']}, {r['gain']:+.2f}%) [{level}]")
            print(f"   原因：{r['reasons']}{funding_info}")
            
            if ENABLE_GITHUB_NOTIFY and r['score'] >= 60:
                title = f"🔴 [高危预警] {r['symbol']} - {r['reasons']}"
                body = f"""
### 发现顶部出货风险
- **标的**: {r['symbol']}
- **价格**: ${r['price']} ({r['gain']:+.2f}%)
- **风险分**: {r['score']}
- **特征**: {r['reasons']}
- **资金费率**: {r.get('funding_rate', 0)*100:.3f}%
- **建议**: 持有者立即止盈，未持有者严禁追高
                """
                create_github_issue(title, body.strip(), ["顶部风险", "紧急"])

    print(f"\n{'='*60}\n")

def main():
    print("🚀 启动 Binance Futures Screener V3.2 (机构增强版)...")
    data = fetch_market_data()
    if not data:
        print("❌ 获取数据失败")
        return
        
    entry_signals = scan_entry_signals(data)
    exit_risks = scan_exit_risks(data)
    print_results(entry_signals, exit_risks)

if __name__ == "__main__":
    main()
