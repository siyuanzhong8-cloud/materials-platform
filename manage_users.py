"""命令行管理账号（在运行服务的电脑上使用）。

用法：
  python manage_users.py list
  python manage_users.py add <账号> [viewer|admin]     # 会提示输入密码，不显示在屏幕上
  python manage_users.py passwd <账号>
  python manage_users.py delete <账号>
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import auth  # noqa: E402


def ask():
    p1 = getpass.getpass("密码（至少 8 位）：")
    if p1 != getpass.getpass("再输一次："):
        sys.exit("两次密码不一致")
    return p1


def main(argv):
    auth.init_db()
    if not argv or argv[0] == "list":
        with auth.connect() as c:
            for r in c.execute("SELECT username, role FROM users ORDER BY role DESC, username"):
                print(f"{r['username']}\t{auth.ROLES[r['role']]}")
        return
    cmd, name = argv[0], (argv[1] if len(argv) > 1 else "")
    if not name:
        sys.exit(__doc__)
    if cmd == "add":
        auth.create_user(name, ask(), argv[2] if len(argv) > 2 else "viewer")
    elif cmd == "passwd":
        auth.set_password(name, ask())
    elif cmd == "delete":
        with auth.connect() as c:
            c.execute("DELETE FROM sessions WHERE username=?", (name,))
            c.execute("DELETE FROM users WHERE username=?", (name,))
    else:
        sys.exit(__doc__)
    print("完成")


if __name__ == "__main__":
    main(sys.argv[1:])
