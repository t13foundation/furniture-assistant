from __future__ import annotations

import argparse
import getpass

from src.webapp.auth import add_user, load_users, remove_user


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage web app users")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("username")
    add.add_argument("--password")
    remove = commands.add_parser("remove")
    remove.add_argument("username")
    commands.add_parser("list")
    args = parser.parse_args()
    if args.command == "add":
        password = args.password or getpass.getpass("Password: ")
        if not password:
            parser.error("Password cannot be empty")
        add_user(args.username, password)
    elif args.command == "remove":
        remove_user(args.username)
    else:
        for username in sorted(load_users()):
            print(username)


if __name__ == "__main__":
    main()
