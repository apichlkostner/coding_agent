from helpers import build_message


def main() -> str:
    message = build_message("world")
    return message


if __name__ == "__main__":
    print(main())
