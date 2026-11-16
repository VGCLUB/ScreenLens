from screenlens.tracking import SubtitleTracker


def test_ocr_misses_do_not_clear_caption_until_confirmed():
    tracker = SubtitleTracker(800)
    assert tracker.observe("Hello there", 0) == "Hello there"
    assert tracker.observe("", 100) is None
    assert tracker.observe("", 400) is None
    assert tracker.observe("Hello there", 600) is None
    assert tracker.observe("", 800) is None
    assert tracker.observe("", 1500) is None
    assert tracker.observe("", 1600) == ""


def test_new_caption_requires_confirmation_and_ocr_noise_is_ignored():
    tracker = SubtitleTracker()
    assert tracker.observe("This is the first subtitle.", 0)
    assert tracker.observe("This is the first subtitle!", 200) is None
    assert tracker.observe("Completely different next sentence", 400) is None
    assert tracker.observe("Completely different next sentence", 550) == "Completely different next sentence"
