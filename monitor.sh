#!/bin/bash
# 本地持续监控脚本 (每5分钟运行一次)

LOG_FILE="monitor.log"
SCRIPT="binance_screener.py"

echo "🚀 启动本地监控服务..."
echo "日志文件: $LOG_FILE"
echo "按 Ctrl+C 停止查看日志 (脚本仍在后台运行)"

# 后台循环运行
while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TIMESTAMP] 开始扫描..." >> $LOG_FILE
    
    # 执行脚本并捕获输出
    python3 $SCRIPT >> $LOG_FILE 2>&1
    
    # 等待5分钟 (300秒)
    sleep 300
done
