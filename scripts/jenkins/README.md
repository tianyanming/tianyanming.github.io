# Jenkins 安卓打包流水线约定

## 角色

| 角色 | 路径 | 说明 |
|------|------|------|
| **主流水线** | `/job/android-pack/`（安卓打包） | 唯一先改、先验证的配置源 |
| **子流水线** | `/job/<用户>/job/android-pack/` | 各人员目录下的同名任务 |

## 修改流程

1. 只改主流水线 `android-pack`
2. 用主流水线打一次包 / 看钉钉通知，确认没问题
3. 再同步到全部子流水线

## 快速同步

脚本会把**主配置**推到各子任务，并自动处理子任务差异：

- `displayName` → `安卓打包 · <用户>`
- Active Choices 的 `projectFullName` → `<用户>/android-pack`
- 保留（或生成）该用户的权限矩阵

```bash
# 需要能访问 Jenkins（本环境可走本地 SS 代理）
export JENKINS_USER=admin
export JENKINS_PASS='***'
export ALL_PROXY=socks5h://127.0.0.1:1080   # 可选

cd scripts/jenkins

# 查看子流水线列表
python3 sync-android-pack-to-children.py --list

# 先演练（不写回）
python3 sync-android-pack-to-children.py --dry-run

# 同步全部子流水线
python3 sync-android-pack-to-children.py

# 只同步某一个
python3 sync-android-pack-to-children.py --only chenweili
```

也可：

```bash
./sync-android-pack.sh              # 同步全部
./sync-android-pack.sh --dry-run
./sync-android-pack.sh --only tangxu
```
