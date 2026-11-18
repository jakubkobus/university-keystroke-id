"""Create participants and print their invite codes (shown only once).

    python -m scripts.create_user U01 U02 U03
    docker compose exec app python -m scripts.create_user U01 U02 U03

Codes are stored only as hashes; copy them from the output and hand them out
(e.g. as a personal link). Keep the code -> person mapping OUTSIDE the server.
"""
import os
import sys

from data import database as db


def main(user_ids: list[str]) -> int:
    if not user_ids:
        print(__doc__)
        return 2
    path = os.environ.get("KEYSTROKEID_DB", "storage/keystrokeid.db")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db.init_db(path)
    rc = 0
    for uid in user_ids:
        try:
            db.add_user(uid, path)
            print(f"{uid}\t{db.create_invite(uid, path)}")
        except ValueError as e:
            print(f"{uid}\tERROR: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
