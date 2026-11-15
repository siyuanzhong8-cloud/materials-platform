# 材料数据库平台 — 数据文件

本目录存放平台各栏目的数据库压缩包。单个文件超过 GitHub 100 MB 上限，因此已用 xz 压缩并按不超过 90 MB 分卷。

**不包含**登录账号库 `config/auth.sqlite`。本地运行前请自行用 `manage_users.py` 创建管理员账号。

## 恢复方法

在本目录执行：

```bash
python3 restore.py ./restored
```

然后将 `restored/` 里的 `*.sqlite` 和说明文件复制到项目的 `data/` 目录。

`manifest.json` 记录了每个文件的大小和 SHA-256，恢复脚本会自动校验。
