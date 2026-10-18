import random
from fbc.weights import default_weights, score_video, update_weights, pick_formats, FORMATS


def test_score_video():
    assert round(score_video(0.8, 0.1, 0.1, 0.01), 3) == 0.441


def test_pick_formats_covers_all_and_explores():
    w = default_weights()
    picks, explore = pick_formats(w, 14, random.Random(1), return_explore=True)
    assert len(picks) == 14 and set(picks) == set(FORMATS)
    assert explore == 3


def test_update_weights_moves_toward_winner():
    w = default_weights()
    scored = [{"format": "zoom_reveal", "category": "animals", "hook_id": "h01", "score": 0.9},
              {"format": "riddle", "category": "animals", "hook_id": "h01", "score": 0.1}]
    w2 = update_weights(w, scored)
    assert w2["formats"]["zoom_reveal"] > w["formats"]["zoom_reveal"]
    assert w2["formats"]["riddle"] < w["formats"]["riddle"]
    for group in ("formats", "categories"):
        assert abs(sum(w2[group].values()) - 1) < 1e-9
        assert min(w2[group].values()) >= 0.05 - 1e-9
