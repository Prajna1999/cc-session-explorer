import sys

from . import capture, install, materialize


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: cc-commit-context <capture|materialize|install> [args...]")
        sys.exit(0 if len(sys.argv) >= 2 else 2)

    subcommand, rest = sys.argv[1], sys.argv[2:]
    if subcommand == "capture":
        sys.exit(capture.run(rest))
    elif subcommand == "materialize":
        sys.exit(materialize.run(rest))
    elif subcommand == "install":
        sys.exit(install.run(rest))
    else:
        print(f"unknown subcommand: {subcommand!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
