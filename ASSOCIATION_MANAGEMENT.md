# 地址关联数据管理指南

## 概述

本指南介绍如何管理和清理 Tron 能量机器人的地址关联数据。地址关联功能会自动将有问题的能量提供方与收款地址进行关联，当提供方被加入黑名单时，相关的收款地址也会被自动标记。

## 关联数据说明

### 数据类型
- **关联记录表** (`blacklist_associations`): 记录地址之间的关联关系
- **自动关联黑名单**: 由关联功能自动添加的黑名单地址 (type='auto_associated')
- **手动黑名单**: 用户手动添加的黑名单地址 (type='manual')
- **白名单**: 包含单地址白名单和地址组合白名单

### 清理选项
1. **清理所有关联数据** (`--clear-all`): 删除关联记录 + 移除自动关联的黑名单
2. **仅清理关联表** (`--clear-associations-only`): 只删除关联记录，保留自动添加的黑名单

## 使用说明

### 1. 查看当前状态

```bash
# 基本统计信息
python verify_associations.py

# 详细信息（包含最近的记录）
python verify_associations.py --detailed

# 导出数据到CSV文件
python verify_associations.py --export
```

### 2. 备份数据

在执行任何清理操作前，强烈建议先备份数据：

```bash
python clear_associations.py --backup-only
```

备份文件会保存为 `associations_backup_YYYYMMDD_HHMMSS.json` 格式。

### 3. 清理关联数据

#### 完全重置（推荐）
清空所有关联数据，从头开始积累：

```bash
# 预览操作
python clear_associations.py --clear-all --dry-run

# 实际执行
python clear_associations.py --clear-all
```

#### 仅清理关联表
保留自动添加的黑名单，只清空关联关系：

```bash
# 预览操作
python clear_associations.py --clear-associations-only --dry-run

# 实际执行
python clear_associations.py --clear-associations-only
```

### 4. 恢复数据

如果需要恢复到之前的状态：

```bash
python clear_associations.py --restore associations_backup_20240101_120000.json
```

### 5. 强制执行（跳过确认）

```bash
python clear_associations.py --clear-all --force
```

## 常见使用场景

### 场景1: 重新开始关联（推荐流程）

当您想要清空所有关联数据重新开始时：

```bash
# 1. 查看当前状态
python verify_associations.py

# 2. 备份数据
python clear_associations.py --backup-only

# 3. 预览清理操作
python clear_associations.py --clear-all --dry-run

# 4. 执行清理
python clear_associations.py --clear-all

# 5. 重启机器人
# 新的关联将从此开始积累
```

### 场景2: 只重置关联关系

如果您想保留已有的黑名单，只重置关联关系：

```bash
# 1. 备份数据
python clear_associations.py --backup-only

# 2. 清理关联表
python clear_associations.py --clear-associations-only

# 3. 重启机器人
```

### 场景3: 数据分析

导出数据进行分析：

```bash
python verify_associations.py --export
```

导出的CSV文件将保存在 `exports/` 目录中。

## 注意事项

1. **备份重要性**: 在执行任何清理操作前，务必先备份数据
2. **重启机器人**: 清理操作完成后需要重启机器人以应用更改
3. **预览功能**: 使用 `--dry-run` 参数可以预览操作而不实际执行
4. **数据恢复**: 如果出现问题，可以使用备份文件恢复数据
5. **权限要求**: 需要数据库操作权限

## 文件说明

- `clear_associations.py`: 主要的清理工具
- `verify_associations.py`: 数据状态查看和验证工具
- `association_management_example.py`: 使用示例和演示脚本

## 帮助信息

查看完整的命令行选项：

```bash
python clear_associations.py --help
python verify_associations.py --help
```

## 故障排除

### 问题1: 模块导入错误
确保在虚拟环境中运行：
```bash
source venv/bin/activate
python clear_associations.py --help
```

### 问题2: 数据库连接失败
检查 `.env` 文件中的 `DATABASE_URL` 配置是否正确。

### 问题3: 权限不足
确保具有数据库的读写权限。

---

*更新时间: 2024年8月*
