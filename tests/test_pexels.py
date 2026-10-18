import pytest
from fbc import pexels


def vid(i, w, h, dur=10):
    return {"id": i, "duration": dur, "video_files": [
        {"width": w // 2, "height": h // 2, "link": f"https://v/{i}/small.mp4", "file_type": "video/mp4"},
        {"width": w, "height": h, "link": f"https://v/{i}/hd.mp4", "file_type": "video/mp4"}]}


class R:
    def __init__(self, data):
        self._d, self.status_code = data, 200

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "px")


def route(monkeypatch, videos_by_orientation, photos=()):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if "videos" in url:
            return R({"videos": videos_by_orientation.get(params.get("orientation"), [])})
        return R({"photos": list(photos)})
    monkeypatch.setattr(pexels.requests, "get", get)
    return calls


def test_portrait_video_chosen(monkeypatch):
    route(monkeypatch, {"portrait": [vid(1, 1080, 1920)]})
    c = pexels.find_clip("horse", "portrait", set())
    assert c.kind == "video" and c.pexels_id == 1 and c.url.endswith("hd.mp4")


def test_excluded_skipped(monkeypatch):
    route(monkeypatch, {"portrait": [vid(1, 1080, 1920), vid(2, 1080, 1920)]})
    assert pexels.find_clip("horse", "portrait", {1}).pexels_id == 2


def test_low_res_skipped(monkeypatch):
    route(monkeypatch, {"portrait": [vid(1, 540, 960), vid(3, 1080, 1920)]})
    assert pexels.find_clip("horse", "portrait", set()).pexels_id == 3


def test_falls_back_to_landscape(monkeypatch):
    route(monkeypatch, {"landscape": [vid(5, 1920, 1080)]})
    c = pexels.find_clip("horse", "portrait", set())
    assert c.kind == "video" and c.pexels_id == 5 and c.width > c.height


def test_falls_back_to_generic_quiz_video_before_photo(monkeypatch):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append(params.get("query"))
        if "videos" in url and params["query"] in pexels.GENERIC_QUERIES:
            return R({"videos": [vid(77, 1080, 1920)]})
        return R({"videos": [], "photos": [{"id": 9, "width": 4000, "height": 6000, "src": {"original": "x"}}]})
    monkeypatch.setattr(pexels.requests, "get", get)
    c = pexels.find_clip("very rare subject", "portrait", set())
    assert c.kind == "video" and c.pexels_id == 77


def test_falls_back_to_photo(monkeypatch):
    route(monkeypatch, {}, photos=[{"id": 9, "width": 4000, "height": 6000, "src": {"original": "https://p/9.jpg", "large2x": "https://p/9l.jpg"}}])
    c = pexels.find_clip("horse", "portrait", set())
    assert c.kind == "photo" and c.pexels_id == 9


def test_nothing_raises(monkeypatch):
    route(monkeypatch, {})
    with pytest.raises(pexels.NoVisual):
        pexels.find_clip("zzz", "portrait", set())
