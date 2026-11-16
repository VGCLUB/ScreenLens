from difflib import SequenceMatcher


def same_text(first: str, second: str, threshold=0.9):
    a, b = "".join(first.split()), "".join(second.split())
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= threshold)


class SubtitleTracker:
    """Debounce changed text and tolerate short OCR misses without flicker."""
    def __init__(self, missing_ms=800):
        self.current = None
        self.candidate = ""
        self.candidate_count = 0
        self.missing_since = None
        self.missing_ms = missing_ms

    @property
    def needs_confirmation(self):
        return self.missing_since is not None or self.candidate_count > 0

    def observe(self, text, now_ms):
        if self.current is None:
            self.current = text
            return text
        if text and same_text(text, self.current):
            self.missing_since = None
            self.candidate_count = 0
            return None
        if not text:
            self.candidate_count = 0
            if not self.current:
                return None
            if self.missing_since is None:
                self.missing_since = now_ms
            if now_ms-self.missing_since >= self.missing_ms:
                self.current = ""
                self.missing_since = None
                return ""
            return None
        self.missing_since = None
        if not self.current:
            self.current = text
            self.candidate_count = 0
            return text
        self.candidate_count = self.candidate_count+1 if same_text(text, self.candidate) else 1
        self.candidate = text
        if self.candidate_count >= 2:
            self.current = text
            self.candidate_count = 0
            return text
        return None
