class Greeter:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hello, {self.name}!"


def build_message(name: str) -> str:
    greeter = Greeter(name)
    return greeter.greet()
