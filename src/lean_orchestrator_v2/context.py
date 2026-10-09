"""Bounded navigation notes alongside complete mathematical artifacts."""


class TokenCounter:
    def __init__(self, encoding: str = "utf8") -> None:
        self.name = encoding
        self.encoder = None
        if encoding == "o200k_base":
            try:
                import tiktoken
            except ImportError as error:
                raise ValueError("o200k_base requires the optional dependency: pip install 'lean-orchestrator[v2]'") from error
            self.encoder = tiktoken.get_encoding(encoding)
        elif encoding != "utf8":
            raise ValueError(f"unknown tokenizer {encoding}")

    def count(self, text: str) -> int:
        return len(self.encoder.encode(text, disallowed_special=())) if self.encoder else len(text.encode("utf-8"))

    def clip(self, text: str, limit: int) -> str:
        if self.encoder:
            tokens = self.encoder.encode(text, disallowed_special=())
            clipped = self.encoder.decode(tokens[:limit])
            # A truncated UTF-8 token sequence may decode to a replacement character.
            # Re-encoding that character can cost more tokens than the original prefix.
            while self.count(clipped) > limit:
                tokens = tokens[:max(0, len(tokens[:limit]) - 1)]
                clipped = self.encoder.decode(tokens)
            return clipped
        # Every BPE token consumes at least one byte: this stricter offline cap
        # also satisfies the configured token bound without pretending to tokenize.
        return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
