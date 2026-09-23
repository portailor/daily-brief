import json

from brief.deliver import queue

ITEM = {"id": "a", "from": "2026-09-28", "until": "2026-10-02", "mood": "down"}


def test_기간_안_파란날에만_올린다():
    assert queue.due([ITEM], "2026-09-29", "down", []) == ITEM
    assert queue.due([ITEM], "2026-09-29", "up", []) is None          # 빨간 자켓 날
    assert queue.due([ITEM], "2026-09-26", "down", []) is None        # 시작 전 (추석 연휴)
    assert queue.due([ITEM], "2026-10-03", "down", []) is None        # 기간 지남
    assert queue.due([ITEM], "2026-09-28", "down", []) == ITEM        # 양 끝 포함
    assert queue.due([ITEM], "2026-10-02", "down", []) == ITEM


def test_한번_올린_영상은_다시_올리지_않는다():
    assert queue.due([ITEM], "2026-09-30", "down", ["a"]) is None


def test_조건_없는_항목과_순서():
    b = {"id": "b"}
    assert queue.due([ITEM, b], "2026-09-29", "up", []) == b
    assert queue.due([ITEM, b], "2026-09-29", "down", []) == ITEM


def test_실제_대기열_파일이_올바르다():
    items = queue.load()
    assert len({it["id"] for it in items}) == len(items)
    for it in items:
        assert (queue.ROOT / it["file"]).exists(), it["file"]
        assert it["title"] and len(it["title"]) <= 100
        assert it.get("mood") in (None, "up", "down")
        json.dumps(it)
