"""Constructor-injected application service discovered by Wireup."""

from wireup import injectable


@injectable
class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}"
