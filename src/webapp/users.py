from __future__ import annotations

import argparse
import getpass

from src.webapp.auth import ROLES, add_user, load_users, remove_user, set_user_role


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage web app users")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("username")
    add.add_argument("--password")
    add.add_argument("--role", choices=sorted(ROLES), default="admin")
    set_role = commands.add_parser("set-role")
    set_role.add_argument("username")
    set_role.add_argument("role", choices=sorted(ROLES))
    remove = commands.add_parser("remove")
    remove.add_argument("username")
    commands.add_parser("list")
    args = parser.parse_args()
    if args.command == "add":
        password = args.password or getpass.getpass("Password: ")
        if not password:
            parser.error("Password cannot be empty")
        add_user(args.username, password, role=args.role)
    elif args.command == "set-role":
        set_user_role(args.username, args.role)
    elif args.command == "remove":
        remove_user(args.username)
    else:
        for username, record in sorted(load_users().items()):
            print(f"{username}\t{record.get('role', 'admin')}")


if __name__ == "__main__":
    main()
