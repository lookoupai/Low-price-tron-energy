# TRON低价能量地址查找器

这是一个自动化工具，用于查找TRON网络上的低价能量地址。该工具通过分析区块链上的交易数据，自动识别并验证低成本的TRON能量地址。

## 功能特点

- 自动获取最新区块信息
- 分析代理资源交易
- 验证地址的能量使用情况
- 识别低价能量地址
- 输出详细的地址信息和状态

## 安装要求

- Python 3.7+
- pip（Python包管理器）

## 安装步骤

1. 克隆仓库：
```bash
git clone [repository-url]
cd tron-energy-finder
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

## 使用方法

直接运行主程序：
```bash
python tron_energy_finder.py
```

## 输出说明

程序将输出以下格式的信息：

```
🎉 找到以下低价能量地址：

🔹 【能量地址】: [TRON地址]
🔹 【购买记录】: [TronScan链接]
🔹 【购买金额】: [TRX金额]
🔹 【能量数量】: [能量数值]

【地址信息】[使用状态]
```

## 注意事项

- 该工具仅供参考，请勿用于非法用途
- 建议遵守API的使用限制
- 交易前请自行验证地址的可用性

## 许可证

MIT License 