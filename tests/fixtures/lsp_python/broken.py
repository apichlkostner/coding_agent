def expects_int(value: int) -> int:
    return value + 1


BROKEN_RESULT = expects_int("oops")
