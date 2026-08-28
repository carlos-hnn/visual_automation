import numpy as np

from visual_automation.game_states.motion import classify_motion, frame_motion_difference


def test_identical_frames_are_stable() -> None:
    frame = np.zeros((100, 120, 3), dtype=np.uint8)
    assert frame_motion_difference(frame, frame) == 0.0
    assert not classify_motion(frame, frame, threshold=1.0).is_moving


def test_changed_frames_are_moving() -> None:
    before = np.zeros((100, 120, 3), dtype=np.uint8)
    after = np.full((100, 120, 3), 40, dtype=np.uint8)
    status = classify_motion(before, after, threshold=2.0)
    assert status.is_moving
    assert status.difference > 2.0
